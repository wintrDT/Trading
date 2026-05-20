# tests/futures/test_futures_trader.py
import os, tempfile, pytest
from unittest.mock import MagicMock
from bot.futures.db import init_db, get_open_trades
from bot.futures.trader import place_entry, close_trade

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_place_entry_sim_inserts_trade(tmp_db):
    client = MagicMock()
    signal = {'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long', 'price': 5000.25, 'signal_id': 1}
    trade_id = place_entry(client, tmp_db, signal, contracts=1, sim=True)
    assert trade_id is not None
    trades = get_open_trades(tmp_db)
    assert len(trades) == 1
    assert trades[0]['order_id'] == 'SIM'
    assert trades[0]['stop_price'] < 5000.25

def test_place_entry_live_calls_api(tmp_db):
    client = MagicMock()
    client.place_order.return_value = {'orderId': 99, 'orderStatus': 'Filled'}
    client.get_open_position.return_value = None          # no existing broker position
    client.place_stop_order.return_value = {'orderId': 123}  # protective stop placed
    signal = {'symbol': 'ES', 'strategy': 'orb', 'direction': 'short', 'price': 5010.0, 'signal_id': 1}
    trade_id = place_entry(client, tmp_db, signal, contracts=1, sim=False)
    client.place_order.assert_called_once()
    client.place_stop_order.assert_called_once()          # broker-side stop is placed on entry
    assert trade_id is not None
    trades = get_open_trades(tmp_db)
    assert trades[0]['stop_order_id'] == '123'

def test_no_duplicate_entry(tmp_db):
    client = MagicMock()
    client.place_order.return_value = {'orderId': 1}
    signal = {'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long', 'price': 5000.0, 'signal_id': 1}
    place_entry(client, tmp_db, signal, contracts=1, sim=True)
    result = place_entry(client, tmp_db, signal, contracts=1, sim=True)
    assert result is None
