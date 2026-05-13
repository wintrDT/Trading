import os, tempfile, pytest
from bot.futures.db import init_db, insert_signal, insert_trade, get_open_trades, insert_snapshot

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_init_creates_tables(tmp_db):
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert 'futures_trades' in tables
    assert 'futures_signals' in tables
    assert 'futures_snapshots' in tables
    assert 'futures_settings' in tables

def test_insert_and_get_trade(tmp_db):
    trade = {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.25, 'entry_ts': '2026-01-01T10:00:00',
        'contracts': 1, 'stop_price': 4998.25, 'target_price': 5004.25,
        'order_id': 'TEST123', 'status': 'open',
    }
    trade_id = insert_trade(tmp_db, trade)
    assert trade_id > 0
    open_trades = get_open_trades(tmp_db)
    assert len(open_trades) == 1
    assert open_trades[0]['symbol'] == 'ES'
