"""Deterministic daily indicators. Missing observations never become zero."""
import math
import pandas as pd

# JSON non ammette NaN/Infinity: un numero non valido diventa un dato mancante.
def finite(value):
    try:
        value = float(value)
        return round(value, 6) if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None

def wilder(series, n=14):
    if len(series) < n:
        return pd.Series(index=series.index, dtype=float)
    result = pd.Series(float('nan'), index=series.index)
    # Wilder: prima media semplice di n valori, poi aggiornamenti con peso 1/n.
    result.iloc[n-1] = series.iloc[:n].mean()
    for i in range(n, len(series)):
        result.iloc[i] = (result.iloc[i-1] * (n-1) + series.iloc[i]) / n
    return result

def analyze(frame, category):
    # Inizializzare a None evita di confondere un dato assente con un valore pari a zero.
    metrics = dict.fromkeys(['sma20', 'sma50', 'sma200', 'rsi', 'atr', 'atr_pct', 'bollinger_low', 'bollinger_high', 'volatility', 'volume_ratio'])
    out = {'interest': 'Dati Insufficienti', 'risk': 'Dati Insufficienti', 'score': None, 'metrics': metrics, 'positive': [], 'negative': []}
    if frame.empty or 'Close' not in frame:
        out['negative'] = ['Serie storica non disponibile.']
        return out
    f = frame.loc[frame.Close.notna() & (frame.Close > 0)].sort_index()
    c = f.Close.astype(float)
    if len(c) < 20:
        out['negative'] = ['Servono almeno 20 osservazioni giornaliere valide.']
        return out
    # Le finestre contano osservazioni disponibili: sedute per azioni, giorni per crypto.
    for n in (20, 50, 200):
        metrics['sma' + str(n)] = finite(c.rolling(n).mean().iloc[-1])
    delta = c.diff().dropna()
    gain = wilder(delta.clip(lower=0)).iloc[-1]
    loss = wilder(-delta.clip(upper=0)).iloc[-1]
    # Serie piatta → RSI 50; solo rialzi e nessuna perdita → RSI 100.
    metrics['rsi'] = finite(50 if loss == gain == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain/loss))
    std = c.rolling(20).std(ddof=0).iloc[-1]
    metrics['bollinger_low'] = finite(metrics['sma20'] - 2*std)
    metrics['bollinger_high'] = finite(metrics['sma20'] + 2*std)
    metrics['volatility'] = finite(c.pct_change().tail(20).std() * math.sqrt(365 if category == 'Crypto' else 252) * 100)
    # ATR richiede massimi/minimi reali: non ricostruirli da semplici campioni di prezzo.
    if {'High', 'Low'}.issubset(f.columns) and f[['High', 'Low']].tail(15).notna().all().all():
        tr = pd.concat([f.High-f.Low, (f.High-c.shift()).abs(), (f.Low-c.shift()).abs()], axis=1).max(axis=1)
        metrics['atr'] = finite(wilder(tr).iloc[-1])
        metrics['atr_pct'] = finite(metrics['atr']/c.iloc[-1]*100)
    if 'Volume' in f and len(f) >= 21:
        # Esclude il volume corrente dalla media usata come confronto.
        prior = f.Volume.iloc[-21:-1]
        if prior.notna().all() and prior.mean() > 0:
            metrics['volume_ratio'] = finite(f.Volume.iloc[-1]/prior.mean())
    # Il rischio dipende dalla volatilità, indipendentemente dal punteggio di interesse.
    vol = metrics['volatility']
    out['risk'] = 'Dati Insufficienti' if vol is None else 'Elevato' if vol >= 45 else 'Moderato' if vol >= 20 else 'Basso'
    if len(c) < 200:
        out['negative'].append('Meno di 200 osservazioni: trend lungo incompleto, interesse non classificabile.')
        return out
    # Regole euristiche trasparenti: il punteggio non è una probabilità di rendimento.
    score = 50
    for key, weight in [('sma20', 10), ('sma50', 15), ('sma200', 20)]:
        above = c.iloc[-1] > metrics[key]
        score += weight if above else -weight
        out['positive' if above else 'negative'].append('Prezzo ' + ('sopra ' if above else 'sotto ') + key.upper() + '.')
    rsi = metrics['rsi']
    if 45 <= rsi <= 65:
        score += 10
        out['positive'].append('RSI tra 45 e 65: momentum equilibrato.')
    elif rsi > 70:
        score -= 15
        out['negative'].append('RSI sopra 70: possibile ipercomprato.')
    elif rsi < 30:
        score -= 10
        out['negative'].append('RSI sotto 30: debolezza, non un segnale automatico di acquisto.')
    ratio = metrics['volume_ratio']
    if ratio is not None and ratio >= 1.5:
        up = c.iloc[-1] > c.iloc[-2]
        score += 10 if up else -10
        out['positive' if up else 'negative'].append('Volume ≥1,5× la media precedente con prezzo ' + ('in rialzo.' if up else 'in calo.'))
    if ratio is None:
        out['negative'].append('Volume confrontabile non disponibile.')
    if metrics['atr'] is None:
        out['negative'].append('ATR non disponibile: mancano massimi/minimi giornalieri.')
    if out['risk'] == 'Elevato':
        out['negative'].append('Volatilità annualizzata elevata (≥45%).')
    out['score'] = max(0, min(100, score))
    out['interest'] = 'Elevato' if score >= 75 else 'Moderato' if score >= 60 else 'Attenzione' if score < 35 else 'Neutrale'
    return out
