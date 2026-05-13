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

def test_get_daily_pnl(tmp_db):
    from bot.futures.db import update_trade_closed
    from datetime import datetime, timezone
    trade_id = insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-05-13T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    update_trade_closed(tmp_db, trade_id, close_price=5002.0, close_reason='profit_target',
                        close_ts='2026-05-13T11:00:00', pnl=100.0)
    from bot.futures.db import get_daily_pnl
    assert get_daily_pnl(tmp_db, '2026-05-13') == 100.0
    assert get_daily_pnl(tmp_db, '2026-05-14') == 0.0

def test_get_all_time_pnl(tmp_db):
    from bot.futures.db import update_trade_closed, get_all_time_pnl
    for i, pnl in enumerate([100.0, -50.0]):
        tid = insert_trade(tmp_db, {
            'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
            'entry_price': 5000.0, 'entry_ts': f'2026-05-1{i+3}T10:00:00',
            'stop_price': 4998.0, 'target_price': 5004.0,
            'contracts': 1, 'order_id': 'SIM', 'status': 'open',
        })
        update_trade_closed(tmp_db, tid, close_price=5002.0, close_reason='test',
                            close_ts=f'2026-05-1{i+3}T11:00:00', pnl=pnl)
    assert get_all_time_pnl(tmp_db) == 50.0
