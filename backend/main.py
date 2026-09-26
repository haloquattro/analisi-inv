import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from backend.models import ROOT, Asset, Watch, Alert, History, Cache, Session, now
from backend.services import data_fetcher as fetch
from backend.services.analyzer import analyze
from backend.services.notifier import notify

# Una sola scansione per processo: avviare Uvicorn con un unico worker.
log = logging.getLogger('radar')
SCAN_INTERVAL = max(900, int(os.getenv('SCAN_INTERVAL_SECONDS', '3600')))
scan_lock = threading.Lock()
asset_lock = threading.RLock()
state = {'running':False, 'last_scan':None, 'errors':[], 'observed':0, 'candidates':0, 'ids':[]}
# Ripristina i risultati precedenti per mostrare subito la dashboard al riavvio.
with Session() as session:
    previous = session.get(Cache, 'scan-state')
    if previous:
        state.update(previous.payload)
        state['running'] = False

def check_id(asset_id):
    try:
        fetch.validate(asset_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

def save_analysis(asset_id, initial=None, force=False):
    # One writer for each computation/alert transition, including concurrent detail requests.
    with asset_lock:
        with Session() as s:
            existing = s.get(Asset, asset_id)
            if existing and not force:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(existing.payload['updated_at'])).total_seconds()
                if age < 900:
                    return existing.payload
        quote, frame = fetch.details(asset_id, initial)
        quote['analysis'] = analyze(frame, quote['category'])
        with Session() as s:
            s.merge(Asset(id=asset_id, payload=quote))
            # One history entry per observation set, not per page visit.
            last = s.scalar(select(History).where(History.asset_id == asset_id).order_by(History.id.desc()).limit(1))
            hist = {'analysis_asof':quote['analysis_asof'], **quote['analysis']}
            if not last or last.payload != hist:
                s.add(History(asset_id=asset_id, payload=hist))
            old_ids = select(History.id).where(History.asset_id == asset_id).order_by(History.id.desc()).offset(200)
            s.execute(delete(History).where(History.id.in_(old_ids)))
            alert = s.get(Alert, asset_id)
            if alert:
                level = quote['analysis']['interest']
                valid = level != 'Dati Insufficienti'
                # Il superamento è assoluto: valgono sia rialzi sia ribassi oltre soglia.
                crossed = quote['change'] is not None and alert.threshold is not None and abs(quote['change']) >= alert.threshold
                messages = []
                if valid and alert.interest and alert.last_interest and level != alert.last_interest:
                    messages.append('Interesse: ' + alert.last_interest + ' → ' + level)
                # Notifica solo il passaggio sotto→sopra soglia; il rientro riarma la regola.
                if crossed and not alert.above:
                    messages.append('Variazione ' + quote['change_label'] + ': ' + str(round(quote['change'], 2)) + '%')
                sent = not messages or notify('analisi inv · ' + quote['symbol'], '; '.join(messages))
                # Un invio fallito non consuma la transizione: verrà ritentata.
                if sent:
                    if valid:
                        alert.last_interest = level
                    if quote['change'] is not None:
                        alert.above = crossed
                    if messages:
                        alert.last_sent = now()
                elif messages:
                    log.warning('Notifica non consegnata per %s; verrà ritentata.', asset_id)
            s.commit()
        return quote

def scan():
    if not scan_lock.acquire(blocking=False):
        return
    state['running'] = True
    try:
        quotes, errors = fetch.primary()
        # Imbuto: approfondisce al massimo 20 anomalie, senza riempire posti vuoti.
        candidates = sorted((q for q in quotes if q['anomaly'] >= 1), key=lambda q:q['anomaly'], reverse=True)[:20]
        state.update(observed=len(quotes), candidates=len(candidates), errors=errors)
        ids = []
        for q in candidates:
            try:
                save_analysis(q['id'], q, force=True)
                ids.append(q['id'])
            except Exception:
                log.exception('Analisi fallita: %s', q['id'])
                errors.append('Analisi non disponibile: ' + q['symbol'])
        with Session() as s:
            monitored = set(s.scalars(select(Watch.asset_id))) | set(s.scalars(select(Alert.asset_id)))
        # Gli asset seguiti restano monitorati anche se esclusi dai candidati della Home.
        for asset_id in monitored - set(ids):
            try:
                save_analysis(asset_id, force=True)
            except Exception:
                errors.append('Aggiornamento monitoraggio fallito: ' + asset_id)
        state.update(last_scan=now(), ids=ids, errors=errors)
        with Session() as s:
            s.merge(Cache(key='scan-state', created=time.time(), payload=dict(state, running=False)))
            s.commit()
    except Exception:
        log.exception('Scansione fallita')
        state['errors'] = ['Scansione interrotta: consultare il log del server.']
    finally:
        state['running'] = False
        scan_lock.release()

async def schedule():
    while True:
        # Le chiamate sincrone ai provider non devono bloccare le risposte HTTP.
        await asyncio.to_thread(scan)
        await asyncio.sleep(SCAN_INTERVAL)

@asynccontextmanager
async def lifespan(app):
    task = None if os.getenv('RADAR_DISABLE_SCHEDULER') == '1' else asyncio.create_task(schedule())
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(title='analisi inv', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
@app.middleware('http')
async def local_write_guard(request: Request, call_next):
    # La testata personalizzata e il controllo Origin ostacolano scritture da altri siti.
    # Non sostituiscono autenticazione: il servizio deve restare in ascolto su localhost.
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('origin')
        if request.headers.get('x-radar-client') != 'local' or (origin and urlparse(origin).netloc != request.headers.get('host')):
            return JSONResponse({'detail':'Richiesta locale non valida.'}, status_code=403)
    return await call_next(request)

@app.exception_handler(fetch.ProviderError)
async def provider_error(request, exc):
    return JSONResponse({'detail':str(exc)}, status_code=503)

@app.get('/api/status')
def status():
    return {**state, 'interval_seconds':SCAN_INTERVAL, 'universe_size':len(fetch.STOCKS)+31}
@app.post('/api/scan')
def rescan():
    if state['last_scan'] and (datetime.now(timezone.utc)-datetime.fromisoformat(state['last_scan'])).total_seconds() < 900:
        raise HTTPException(429, 'Scansione disponibile ogni 15 minuti per rispettare le quote API.')
    threading.Thread(target=scan, daemon=True).start()
    return {'accepted':True}
@app.get('/api/opportunities')
def opportunities(category: Optional[str]=None, interest: Optional[str]=None, risk: Optional[str]=None):
    with Session() as s:
        items = [r.payload for r in s.scalars(select(Asset).where(Asset.id.in_(state['ids'])))]
    items = [x for x in items if x['analysis']['interest'] in ('Elevato', 'Moderato')]
    for key, value in [('category', category), ('interest', interest), ('risk', risk)]:
        if value:
            items = [x for x in items if (x.get(key) if key == 'category' else x['analysis'][key]) == value]
    return sorted(items, key=lambda x:x['analysis']['score'] or 0, reverse=True)
@app.get('/api/search')
def search(q: str=''):
    if len(q.strip()) < 2 or len(q) > 100:
        raise HTTPException(422, 'Inserisci da 2 a 100 caratteri.')
    return fetch.search(q.strip())
@app.get('/api/asset/{asset_id}')
def asset(asset_id: str):
    check_id(asset_id)
    try:
        data = save_analysis(asset_id)
    except Exception as exc:
        # In caso di errore conserva il dato salvato, marcandolo esplicitamente come vecchio.
        with Session() as s:
            row = s.get(Asset, asset_id)
            if row:
                return dict(row.payload, stale=True, refresh_error='Aggiornamento fallito: visualizzati dati salvati.')
        if isinstance(exc, fetch.ProviderError):
            raise exc
        log.exception('Dettaglio non disponibile')
        raise HTTPException(503, 'Dati insufficienti: provider non disponibile.')
    return data
@app.get('/api/asset/{asset_id}/chart')
def chart(asset_id: str, period: str='1w'):
    check_id(asset_id)
    if period not in ('1d', '1w', '1m', '1y'):
        raise HTTPException(422, 'Periodo non valido.')
    return fetch.chart(asset_id, period)
@app.get('/api/asset/{asset_id}/history')
def history(asset_id: str):
    check_id(asset_id)
    with Session() as s:
        return [{'timestamp':r.timestamp, **r.payload} for r in s.scalars(select(History).where(History.asset_id == asset_id).order_by(History.id.desc()).limit(30))]
@app.get('/api/watchlist')
def watchlist():
    with Session() as s:
        return [s.get(Asset, key).payload if s.get(Asset, key) else {'id':key} for key in s.scalars(select(Watch.asset_id))]
@app.put('/api/watchlist/{asset_id}')
def add_watch(asset_id: str):
    check_id(asset_id)
    with Session() as s:
        if not s.get(Asset, asset_id):
            raise HTTPException(400, 'Apri prima il dettaglio dell’asset.')
        s.merge(Watch(asset_id=asset_id))
        s.commit()
    return {'saved':True}
@app.delete('/api/watchlist/{asset_id}')
def remove_watch(asset_id: str):
    with Session() as s:
        s.execute(delete(Watch).where(Watch.asset_id == asset_id))
        s.commit()
    return {'saved':False}
class AlertInput(BaseModel):
    interest: bool = True
    threshold: Optional[float] = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
@app.get('/api/alerts')
def alerts():
    with Session() as s:
        return [{'id':r.asset_id, 'interest':r.interest, 'threshold':r.threshold, 'last_sent':r.last_sent} for r in s.scalars(select(Alert))]
@app.put('/api/alerts/{asset_id}')
def set_alert(asset_id: str, body: AlertInput):
    check_id(asset_id)
    with Session() as s:
        a = s.get(Asset, asset_id)
        if not a:
            raise HTTPException(400, 'Apri prima il dettaglio dell’asset.')
        level = a.payload['analysis']['interest']
        change = a.payload.get('change')
        # Salvare/modificare una regola prende la situazione attuale come riferimento.
        s.merge(Alert(asset_id=asset_id, interest=body.interest, threshold=body.threshold, last_interest=None if level == 'Dati Insufficienti' else level, above=change is not None and body.threshold is not None and abs(change) >= body.threshold))
        s.commit()
    return {'saved':True}
@app.delete('/api/alerts/{asset_id}')
def delete_alert(asset_id: str):
    with Session() as s:
        s.execute(delete(Alert).where(Alert.asset_id == asset_id))
        s.commit()
    return {'deleted':True}
@app.post('/api/notifications/test')
def test_notification():
    return {'sent':notify('analisi inv', 'Le notifiche locali sono attive.')}
@app.get('/api/compare')
def compare(ids: str):
    keys = list(dict.fromkeys(ids.split(',')))
    if not 2 <= len(keys) <= 3:
        raise HTTPException(422, 'Seleziona 2 o 3 asset distinti.')
    return [asset(key) for key in keys]
@app.get('/api/health')
def health():
    return {'app':'analisi-inv', 'ok':True}
app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')
@app.get('/')
def home():
    return FileResponse(ROOT / 'frontend' / 'index.html')
