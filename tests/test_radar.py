"""Offline tests; synthetic data exists only inside this test module."""
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
os.environ['RADAR_DATA_DIR'] = tempfile.mkdtemp(prefix='radar-test-')
os.environ['RADAR_DISABLE_SCHEDULER'] = '1'
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.services.analyzer import analyze, wilder
from backend.services import notifier
from backend import main
from backend.models import Alert, Session, History, Asset, Cache
from backend.services import data_fetcher as fetch
from sqlalchemy import select

def frame(count=240, descending=False):
    values = np.linspace(100, 160, count)
    if descending:
        values = values[::-1]
    return pd.DataFrame({'Close':values, 'High':values+1, 'Low':values-1, 'Volume':np.full(count, 1000.)}, index=pd.date_range('2024-01-01', periods=count, tz='UTC'))

def quote():
    return {'id':'stock:TEST', 'name':'Test', 'symbol':'TEST', 'category':'Azione', 'price':160, 'change':1., 'change_label':'ultima seduta', 'currency':'USD', 'market':{}, 'updated_at':datetime.now(timezone.utc).isoformat(), 'market_time':None, 'analysis_asof':'2024-08-27', 'errors':[]}

class Indicators(unittest.TestCase):
    def test_missing_is_not_zero(self):
        result=analyze(pd.DataFrame(), 'Azione')
        self.assertIsNone(result['score'])
        self.assertEqual(result['risk'], 'Dati Insufficienti')
        self.assertTrue(all(v is None for v in result['metrics'].values()))
    def test_flat_rsi_and_zero_volume(self):
        f=frame(); f['Close']=100.; f['Volume']=0
        r=analyze(f,'Azione')
        self.assertEqual(r['metrics']['rsi'],50)
        self.assertIsNone(r['metrics']['volume_ratio'])
    def test_wilder_seed(self):
        result=wilder(pd.Series([1.,2.,3.,4.]),3)
        self.assertEqual(result.iloc[2],2)
        self.assertAlmostEqual(result.iloc[3],8/3)
    def test_sma_rsi_atr(self):
        f=frame();r=analyze(f,'Azione')
        self.assertAlmostEqual(r['metrics']['sma20'],f.Close.tail(20).mean(),places=5)
        self.assertEqual(r['metrics']['rsi'],100)
        self.assertEqual(r['metrics']['atr'],2)
        self.assertEqual(r['interest'],'Elevato')
        self.assertEqual(analyze(frame(descending=True),'Azione')['interest'],'Attenzione')
    def test_short_history_and_crypto_no_fabricated_ohlc(self):
        self.assertEqual(analyze(frame(60),'Azione')['interest'],'Dati Insufficienti')
        r=analyze(frame()[['Close']],'Crypto')
        self.assertIsNone(r['metrics']['atr'])
        self.assertIsNone(r['metrics']['volume_ratio'])
    def test_volume_excludes_current_bar(self):
        f=frame();f.iloc[-1,f.columns.get_loc('Volume')]=2000
        self.assertEqual(analyze(f,'Azione')['metrics']['volume_ratio'],2)
    def test_interest_and_risk_independent(self):
        f=frame();f['Close']=f.Close * np.where(np.arange(len(f))%2,1.12,.88)
        r=analyze(f,'Crypto')
        self.assertEqual(r['risk'],'Elevato')
        self.assertEqual(r['interest'],'Elevato')
        self.assertEqual(analyze(frame(),'Azione')['risk'],'Basso')

class APIs(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(main.app)
        self.headers={'X-Radar-Client':'local'}
    def test_routes_and_watchlist(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/api/health').json()['app'],'analisi-inv')
        with patch.object(fetch,'details',return_value=(quote(),frame())):
            r=self.client.get('/api/asset/stock:TEST')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['analysis']['interest'],'Elevato')
        self.assertEqual(self.client.put('/api/watchlist/stock:TEST',headers=self.headers).status_code,200)
        self.assertIn('stock:TEST',[a['id'] for a in self.client.get('/api/watchlist').json()])
        self.assertEqual(self.client.put('/api/alerts/stock:TEST',headers=self.headers,json={'interest':True,'threshold':5}).status_code,200)
        self.assertEqual(self.client.put('/api/alerts/stock:TEST',headers=self.headers,json={'threshold':-1}).status_code,422)
        self.assertEqual(self.client.get('/api/asset/stock:TEST/chart?period=invalid').status_code,422)
        self.assertEqual(self.client.get('/api/compare?ids=stock:TEST').status_code,422)
        self.client.delete('/api/watchlist/stock:TEST',headers=self.headers)
    def test_origin_guard_and_invalid_id(self):
        self.assertEqual(self.client.post('/api/scan').status_code,403)
        self.assertEqual(self.client.post('/api/scan',headers={**self.headers,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/asset/crypto:bad;injection').status_code,422)
    def test_alert_crossing_rearm_no_spam(self):
        q=quote()
        with Session() as s:
            s.merge(Alert(asset_id=q['id'],interest=True,threshold=5,last_interest='Moderato',above=False));s.commit()
        with patch.object(fetch,'details',return_value=(q,frame())),patch.object(main,'notify',return_value=True) as send:
            main.save_analysis(q['id'],force=True)
            self.assertEqual(send.call_count,1)
            main.save_analysis(q['id'],force=True)
            self.assertEqual(send.call_count,1)
            q['change']=6
            main.save_analysis(q['id'],force=True)
            self.assertEqual(send.call_count,2)
            main.save_analysis(q['id'],force=True)
            self.assertEqual(send.call_count,2)
            q['change']=2;main.save_analysis(q['id'],force=True)
            q['change']=-7;main.save_analysis(q['id'],force=True)
            self.assertEqual(send.call_count,3)
    def test_funnel_never_exceeds_twenty(self):
        quotes=[dict(quote(),id='stock:T'+str(i),anomaly=2) for i in range(30)]
        with Session() as s:
            s.query(Alert).delete();s.commit()
        with patch.object(fetch,'primary',return_value=(quotes,[])),patch.object(main,'save_analysis') as save:
            main.scan()
            self.assertEqual(save.call_count,20)
            self.assertEqual(main.state['candidates'],20)
    def test_cached_loader_only_called_once(self):
        with patch('builtins.print') as loader:
            loader.return_value={'value':42}
            self.assertEqual(fetch.cached('test-cache',60,loader),{'value':42})
            self.assertEqual(fetch.cached('test-cache',60,loader),{'value':42})
            self.assertEqual(loader.call_count,1)
    def test_notification_uses_argv(self):
        with patch.object(notifier.platform,'system',return_value='Darwin'),patch.object(notifier.subprocess,'run') as run:
            self.assertTrue(notifier.notify('title','" & do shell script "bad'))
            self.assertEqual(run.call_args.args[0][-1],'" & do shell script "bad')

if __name__=='__main__':
    unittest.main()
