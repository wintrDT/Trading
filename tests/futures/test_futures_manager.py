# tests/futures/test_futures_manager.py
import os, tempfile, sqlite3, pytest
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


def _closed_trade(db_path, tid):
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM futures_trades WHERE id=?', (tid,)).fetchone()
    con.close()
    return dict(row)


def test_reconciles_broker_stop_fill(tmp_db):
    # LIVE trade whose broker stop already fired — manager must record TopStep's
    # ACTUAL fill (-$175 @ 4998.25), not the synthetic quote, even though price is
    # currently within the stop/target range (synthetic stop would NOT trigger).
    tid = insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'LIVE123', 'status': 'open',
        'stop_order_id': 'STOP1',
    })
    client = MagicMock()
    client.get_open_position.return_value = None  # broker shows flat
    client.get_close_fill.return_value = {'price': 4998.25, 'pnl': -175.0, 'fees': 3.8, 'size': 1}
    manage_futures_positions(client, tmp_db, current_prices={'ES': 5001.0}, sim=False)
    assert len(get_open_trades(tmp_db)) == 0          # reconciled closed
    row = _closed_trade(tmp_db, tid)
    assert row['close_reason'] == 'broker_stop'
    assert row['close_price'] == 4998.25              # TopStep's real fill, not 5001
    assert row['pnl'] == -175.0                        # TopStep's realized P&L
    client.get_close_fill.assert_called()


def test_trails_broker_stop_server_side(tmp_db):
    # Live long with a resting broker stop; price advances enough to trail -> the bot
    # should modify the resting broker stop (not just the DB), and keep the trade open.
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5010.0,
        'contracts': 1, 'order_id': 'LIVE1', 'status': 'open',
        'stop_order_id': 'STOP1',
    })
    client = MagicMock()
    client.get_open_position.return_value = {'size': 1, 'side': 'long', 'avgPrice': 5000.0}  # not flat -> no reconcile
    manage_futures_positions(client, tmp_db, current_prices={'ES': 5003.0}, sim=False)
    client.modify_order.assert_called()                # broker stop trailed server-side
    assert len(get_open_trades(tmp_db)) == 1           # still open (price below target, above trailed stop)


def test_no_false_reconcile_when_no_fill(tmp_db):
    # Broker reports flat (could be a transient API blip) but NO closing fill exists
    # -> do NOT close the trade (guards against false-flat readings).
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'LIVE123', 'status': 'open',
        'stop_order_id': 'STOP1',
    })
    client = MagicMock()
    client.get_open_position.return_value = None
    client.get_close_fill.return_value = None          # no confirming fill
    manage_futures_positions(client, tmp_db, current_prices={'ES': 5001.0}, sim=False)
    assert len(get_open_trades(tmp_db)) == 1           # stays open

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
