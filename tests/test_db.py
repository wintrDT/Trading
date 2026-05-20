import sqlite3
import pytest
from bot.db import (
    init_db, insert_scan, insert_trade,
    update_trade_closed, update_trade_status, update_trade_order_id,
    mark_scan_traded, insert_account_snapshot,
    get_open_trades, get_recent_scans, get_all_trades, get_account_snapshot,
)

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path

SAMPLE_SCAN = {
    'ts': '2026-05-11T10:00:00',
    'underlying': 'SPY',
    'strategy': 'bull_put_spread',
    'expiration': '2026-06-06',
    'short_put_strike': 520.0,
    'long_put_strike': 515.0,
    'short_call_strike': None,
    'long_call_strike': None,
    'credit': 1.75,
    'width': 5.0,
    'delta': 0.25,
    'iv_rank': 45.0,
    'dte': 26,
    'traded': 0,
}

SAMPLE_TRADE = {
    'underlying': 'SPY',
    'strategy': 'bull_put_spread',
    'expiration': '2026-06-06',
    'short_put_strike': 520.0,
    'long_put_strike': 515.0,
    'short_call_strike': None,
    'long_call_strike': None,
    'entry_credit': 1.75,
    'entry_ts': '2026-05-11T10:05:00',
    'contracts': 1,
    'order_id': 'TT-123456',
}

def test_init_creates_tables(db_path):
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    # sqlite_sequence is an internal SQLite table created by AUTOINCREMENT; exclude it
    assert {'scans', 'trades', 'account_snapshots'}.issubset(tables)

def test_insert_and_retrieve_scan(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    assert scan_id is not None
    scans = get_recent_scans(db_path, limit=10)
    assert len(scans) == 1
    assert scans[0]['underlying'] == 'SPY'
    assert scans[0]['credit'] == 1.75

def test_insert_trade_appears_in_open(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    assert trade_id is not None
    trades = get_open_trades(db_path)
    assert len(trades) == 1
    assert trades[0]['order_id'] == 'TT-123456'

def test_update_trade_closed_removes_from_open(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_closed(db_path, trade_id,
                        close_credit=0.875,
                        close_reason='profit_target',
                        close_ts='2026-05-11T14:00:00')
    assert get_open_trades(db_path) == []
    all_trades = get_all_trades(db_path)
    assert all_trades[0]['close_reason'] == 'profit_target'

def test_update_trade_status(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_status(db_path, trade_id, 'pending')
    trades = get_open_trades(db_path)
    assert trades[0]['status'] == 'pending'

def test_update_trade_order_id(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_order_id(db_path, trade_id, 'TT-999999')
    trades = get_open_trades(db_path)
    assert trades[0]['order_id'] == 'TT-999999'

def test_mark_scan_traded(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    mark_scan_traded(db_path, scan_id)
    scans = get_recent_scans(db_path)
    assert scans[0]['traded'] == 1

def test_account_snapshot(db_path):
    insert_account_snapshot(db_path, {
        'ts': '2026-05-11T10:00:00',
        'net_liq': 25000.0,
        'cash': 20000.0,
        'open_pnl': 150.0,
        'realized_pnl_today': 75.0,
    })
    snap = get_account_snapshot(db_path)
    assert snap['net_liq'] == 25000.0

def test_get_account_snapshot_none_when_empty(db_path):
    assert get_account_snapshot(db_path) is None
