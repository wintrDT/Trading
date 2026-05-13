# tests/futures/test_futures_manager.py
import os, tempfile, pytest
from unittest.mock import MagicMock
from bot.futures.db import init_db, insert_trade, get_open_trades
from bot.futures.manager import manage_futures_positions

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_closes_on_stop_loss(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 4997.5}, sim=True)
    assert len(get_open_trades(tmp_db)) == 0

def test_closes_on_profit_target(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'orb', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 5004.5}, sim=True)
    assert len(get_open_trades(tmp_db)) == 0

def test_no_close_within_range(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 5001.0}, sim=True)
    assert len(get_open_trades(tmp_db)) == 1
