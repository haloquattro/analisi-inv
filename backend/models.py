"""SQLite persistent cache, watchlist, history and alert state."""
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, JSON, Integer, Float, Boolean, event
from sqlalchemy.orm import declarative_base, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
# Il database resta locale; RADAR_DATA_DIR permette di scegliere una cartella persistente.
DATA = Path(os.environ.get('RADAR_DATA_DIR', str(ROOT / 'data'))).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
engine = create_engine('sqlite:///' + str(DATA / 'radar.sqlite3'), connect_args={'check_same_thread': False, 'timeout': 30})
@event.listens_for(engine, 'connect')
def configure(dbapi, _):
    # WAL permette alle letture della dashboard di convivere meglio con le scritture.
    dbapi.execute('PRAGMA journal_mode=WAL')
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()
def now():
    return datetime.now(timezone.utc).isoformat()
# Ultima osservazione e analisi disponibili per ogni identificatore del provider.
class Asset(Base):
    __tablename__ = 'assets'
    id = Column(String, primary_key=True)
    payload = Column(JSON, nullable=False)
# Lo storico registra le analisi cambiate; main.py conserva le ultime 200 per asset.
class History(Base):
    __tablename__ = 'history'
    id = Column(Integer, primary_key=True)
    asset_id = Column(String, index=True)
    timestamp = Column(String, default=now)
    payload = Column(JSON)
# La watchlist conserva gli identificatori: i dati correnti sono nella tabella assets.
class Watch(Base):
    __tablename__ = 'watchlist'
    asset_id = Column(String, primary_key=True)
# Regole e stato precedente persistono per evitare notifiche duplicate dopo un riavvio.
class Alert(Base):
    __tablename__ = 'alerts'
    asset_id = Column(String, primary_key=True)
    interest = Column(Boolean, default=True)
    threshold = Column(Float, nullable=True)
    last_interest = Column(String, nullable=True)
    above = Column(Boolean, default=False)
    last_sent = Column(String, nullable=True)
# Cache delle risposte dei provider; created è un timestamp Unix per il controllo TTL.
class Cache(Base):
    __tablename__ = 'cache'
    key = Column(String, primary_key=True)
    created = Column(Float)
    payload = Column(JSON)
Base.metadata.create_all(engine)
