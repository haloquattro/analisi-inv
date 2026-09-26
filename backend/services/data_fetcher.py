"""Two-stage discovery, disk TTL cache and globally paced CoinGecko access."""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
import yfinance as yf
from backend.models import Cache, Session, now, DATA
from backend.services.analyzer import finite

yf.set_tz_cache_location(str(DATA / 'yfinance'))

# Editable, fixed large-cap universe. Not represented as a live index ranking.
STOCKS = 'AAPL MSFT NVDA AMZN GOOGL META BRK-B AVGO TSLA JPM WMT LLY V MA XOM COST UNH HD PG JNJ ABBV BAC NFLX CRM ORCL CVX KO MRK CSCO WFC IBM ABT MCD GE CAT AXP PM GS TMO ISRG LIN PEP DIS AMD QCOM AMGN RTX NOW INTU UBER'.split()
custom = Path(__file__).resolve().parents[2] / 'universe.json'
if custom.exists():
    STOCKS = json.loads(custom.read_text())
    if not isinstance(STOCKS, list) or len(STOCKS) != 50 or len(set(STOCKS)) != 50 or not all(isinstance(x, str) and re.fullmatch(r'[A-Z0-9.\-]{1,15}', x) for x in STOCKS):
        raise ValueError('universe.json deve contenere 50 ticker distinti validi.')
CG_LOCK = threading.Lock()
YF_LOCK = threading.RLock()
CG_NEXT = 0.0
CG_BACKOFF = 0.0

class ProviderError(Exception):
    pass

# Il TTL è espresso in secondi. Gli errori del loader non sostituiscono la cache valida.
def cached(key, ttl, loader):
    with Session() as s:
        row = s.get(Cache, key)
        if row and time.time() - row.created < ttl:
            return row.payload
    value = loader()
    with Session() as s:
        s.merge(Cache(key=key, created=time.time(), payload=value))
        s.commit()
    return value

def cg(path, **params):
    def load():
        global CG_NEXT, CG_BACKOFF
        # Un limite condiviso fra ricerca, grafici e scansione evita raffiche concorrenti.
        with CG_LOCK:
            if time.monotonic() < CG_BACKOFF:
                raise ProviderError('CoinGecko temporaneamente in pausa per rate limit.')
            time.sleep(max(0, CG_NEXT - time.monotonic()))
            CG_NEXT = time.monotonic() + 6.1  # <10 requests/min across all endpoints
            headers = {}
            if os.getenv('COINGECKO_API_KEY'):
                headers['x-cg-demo-api-key'] = os.environ['COINGECKO_API_KEY']
            try:
                response = requests.get('https://api.coingecko.com/api/v3/' + path, params=params, headers=headers, timeout=18)
                if response.status_code == 429:
                    try:
                        seconds = float(response.headers.get('Retry-After', '120'))
                    except ValueError:
                        seconds = 120
                    CG_BACKOFF = time.monotonic() + max(120, seconds)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                raise ProviderError('CoinGecko non disponibile o quota API esaurita.') from exc
    ttl = 86400 if path == 'search' else 21600 if 'market_chart' in path and params.get('days') == 365 else 900
    return cached('cg:' + path + json.dumps(params, sort_keys=True), ttl, load)

# Il prefisso distingue ticker Yahoo e ID CoinGecko, anche quando i simboli coincidono.
def validate(asset_id):
    if not re.fullmatch(r'(stock:[A-Za-z0-9.^=\-]{1,30}|gold:GC=F|crypto:[a-z0-9\-]{1,100})', asset_id):
        raise ValueError('Identificatore asset non valido.')
    return asset_id.split(':', 1)

# Normalizza timestamp e valori prima di serializzare i dati in SQLite/JSON.
def records(frame):
    if frame.empty:
        return []
    result = []
    for stamp, row in frame.iterrows():
        dt = pd.Timestamp(stamp)
        if dt.tzinfo is None:
            dt = dt.tz_localize('UTC')
        result.append({'time': dt.isoformat(), **{k: finite(row.get(k)) for k in ('Open', 'High', 'Low', 'Close', 'Volume')}})
    return result

def as_frame(rows):
    if not rows:
        return pd.DataFrame()
    f = pd.DataFrame(rows)
    f.index = pd.to_datetime(f.pop('time'), utc=True)
    return f

def yahoo_history(symbol, period='1y', interval='1d'):
    def load():
        with YF_LOCK:
            f = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, timeout=15)
        if f.empty:
            raise ProviderError('Yahoo Finance: storico non disponibile per ' + symbol)
        return records(f)
    return as_frame(cached('yh:' + symbol + period + interval, 21600 if period == '2y' else 900, load))

def crypto_quote(row):
    return {'id': 'crypto:' + row['id'], 'symbol': row['symbol'].upper(), 'name': row['name'], 'category': 'Crypto', 'currency': 'USD', 'price': finite(row.get('current_price')), 'change': finite(row.get('price_change_percentage_24h')), 'change_label': '24h', 'market_time': row.get('last_updated'), 'market': {'Capitalizzazione': finite(row.get('market_cap')), 'Volume 24h (USD)': finite(row.get('total_volume')), 'Offerta circolante': finite(row.get('circulating_supply')), 'Massimo 24h': finite(row.get('high_24h')), 'Minimo 24h': finite(row.get('low_24h'))}}

def yahoo_quote(symbol, f):
    c = f.Close.dropna()
    if c.empty:
        raise ProviderError('Prezzi non disponibili per ' + symbol)
    return {'id': ('gold:' if symbol == 'GC=F' else 'stock:') + symbol, 'symbol': symbol, 'name': 'Oro · futures COMEX' if symbol == 'GC=F' else symbol, 'category': 'Oro' if symbol == 'GC=F' else 'Azione', 'currency': 'USD' if symbol == 'GC=F' or symbol in STOCKS else None, 'price': finite(c.iloc[-1]), 'change': finite((c.iloc[-1]/c.iloc[-2]-1)*100) if len(c) > 1 else None, 'change_label': 'ultima seduta', 'market_time': c.index[-1].isoformat(), 'market': {'Volume ultima seduta': finite(f.Volume.iloc[-1]) if 'Volume' in f else None}}

# Primo stadio economico: un batch Yahoo e una richiesta CoinGecko per il paniere.
def primary():
    quotes, errors = [], []
    try:
        def batch():
            with YF_LOCK:
                f = yf.download(STOCKS + ['GC=F'], period='3mo', interval='1d', group_by='ticker', auto_adjust=False, threads=4, progress=False, timeout=15)
            return {symbol: records(f[symbol].dropna(subset=['Close'])) for symbol in STOCKS + ['GC=F'] if symbol in f.columns.get_level_values(0)}
        frames = cached('primary-yahoo-v1:' + ','.join(STOCKS), 900, batch)
        missing = []
        for symbol in STOCKS + ['GC=F']:
            f = as_frame(frames.get(symbol, []))
            if f.empty:
                missing.append(symbol)
                continue
            q = yahoo_quote(symbol, f)
            mean = f.Volume.iloc[-21:-1].mean() if len(f) >= 21 else None
            ratio = finite(f.Volume.iloc[-1]/mean) if mean is not None and mean > 0 else None
            q['anomaly'] = max(abs(q['change'] or 0)/2, (ratio or 0)/1.5)
            quotes.append(q)
        if missing:
            errors.append('Yahoo: dati mancanti per ' + ', '.join(missing))
    except Exception:
        errors.append('Yahoo Finance non disponibile per la scansione iniziale.')
    try:
        coins = cg('coins/markets', vs_currency='usd', order='market_cap_desc', per_page=30, page=1, sparkline='false')
        for row in coins:
            q = crypto_quote(row)
            # Cross-sectional turnover, not mislabeled as a historical volume anomaly.
            cap, volume = row.get('market_cap'), row.get('total_volume')
            turnover = volume/cap if cap and volume is not None else 0
            q['anomaly'] = max(abs(q['change'] or 0)/3, turnover/0.20)
            quotes.append(q)
    except Exception:
        errors.append('CoinGecko non disponibile: verificare connessione, chiave Demo e quota API.')
    return quotes, errors

# Secondo stadio: serie lunga e fondamentali per candidati o asset richiesti dall’utente.
def details(asset_id, initial=None):
    kind, symbol = validate(asset_id)
    errors = []
    if kind == 'crypto':
        q = initial
        if q is None:
            rows = cg('coins/markets', vs_currency='usd', ids=symbol)
            if not rows:
                raise ProviderError('Crypto non trovata.')
            q = crypto_quote(rows[0])
        try:
            data = cg('coins/' + symbol + '/market_chart', vs_currency='usd', days=365)
            f = pd.DataFrame(data['prices'], columns=['time', 'Close'])
            f.index = pd.to_datetime(f.pop('time'), unit='ms', utc=True)
            # CoinGecko prices are samples, not OHLC; never manufacture High/Low.
            f = f.resample('1D').last().dropna()
            today = pd.Timestamp.now(tz='UTC').normalize()
            f = f[f.index < today]
        except Exception:
            f = pd.DataFrame()
            errors.append('Storico crypto non disponibile; analisi insufficiente.')
    else:
        recent = yahoo_history(symbol, '1mo')
        q = yahoo_quote(symbol, recent)
        try:
            f = yahoo_history(symbol, '2y')
            # Drop an ongoing session: daily volume and indicators need completed bars.
            today = pd.Timestamp.now(tz='UTC').date()
            f = f[f.index.date < today]
        except Exception:
            f = pd.DataFrame()
            errors.append('Storico lungo non disponibile.')
        q['market'].update(dict.fromkeys(['P/E', 'Capitalizzazione', 'Dividendo annuo/azione', 'EPS'] if kind == 'stock' else ['Open interest', 'Prezzo per oncia troy']))
        try:
            def info_loader():
                with YF_LOCK:
                    return yf.Ticker(symbol).info
            info = cached('info:' + symbol, 86400, info_loader)
            q['name'] = info.get('shortName') or q['name']
            q['currency'] = info.get('currency') or q['currency']
            q['market'].update({'P/E': finite(info.get('trailingPE')), 'Capitalizzazione': finite(info.get('marketCap')), 'Dividendo annuo/azione': finite(info.get('dividendRate')), 'EPS': finite(info.get('trailingEps'))} if kind == 'stock' else {'Open interest': finite(info.get('openInterest')), 'Prezzo per oncia troy': q['price']})
            # Keep the quote bar timestamp: cached fundamentals may be a day old.
        except Exception:
            errors.append('Fondamentali Yahoo non disponibili.')
    q = dict(q)
    q['updated_at'] = now()
    q['analysis_asof'] = f.index[-1].isoformat() if not f.empty else None
    q['errors'] = errors
    return q, f

# I grafici usano finestre e granularità proprie, separate dalle barre degli indicatori.
def chart(asset_id, period):
    kind, symbol = validate(asset_id)
    if period not in ('1d', '1w', '1m', '1y'):
        raise ValueError('Periodo non valido.')
    if kind == 'crypto':
        data = cg('coins/' + symbol + '/market_chart', vs_currency='usd', days={'1d':1, '1w':7, '1m':30, '1y':365}[period])
        return [{'time': datetime.fromtimestamp(t/1000, timezone.utc).isoformat(), 'price': finite(p)} for t, p in data['prices'] if finite(p) is not None]
    p, interval = {'1d':('1d', '5m'), '1w':('5d', '1h'), '1m':('1mo', '1d'), '1y':('1y', '1d')}[period]
    f = yahoo_history(symbol, p, interval)
    if period == '1w':
        f = f[f.index >= pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=7)]
    return [{'time': r['time'], 'price': r['Close']} for r in records(f) if r['Close'] is not None]

def search(query):
    results, errors = [], []
    try:
        def yahoo_search():
            with YF_LOCK:
                return yf.Search(query, max_results=8, news_count=0).quotes
        for r in cached('search-y:' + query.lower(), 86400, yahoo_search):
            if r.get('quoteType') == 'EQUITY' and re.fullmatch(r'[A-Za-z0-9.^=\-]{1,30}', r['symbol']):
                results.append({'id':'stock:' + r['symbol'], 'symbol':r['symbol'], 'name': r.get('shortname') or r.get('longname') or r['symbol'], 'category':'Azione'})
    except Exception:
        errors.append('Ricerca Yahoo non disponibile.')
    try:
        for r in cg('search', query=query).get('coins', [])[:8]:
            results.append({'id':'crypto:' + r['id'], 'symbol':r['symbol'], 'name':r['name'], 'category':'Crypto'})
    except Exception:
        errors.append('Ricerca CoinGecko non disponibile.')
    if any(x in query.lower() for x in ['oro', 'gold', 'gc=f']):
        results.insert(0, {'id':'gold:GC=F', 'symbol':'GC=F', 'name':'Oro · futures COMEX', 'category':'Oro'})
    if not results and re.fullmatch(r'[A-Za-z0-9.^=\-]{1,30}', query):
        results.append({'id':'stock:' + query.upper(), 'symbol':query.upper(), 'name':'Verifica ticker su Yahoo', 'category':'Azione'})
    return {'items':results, 'errors':errors}
