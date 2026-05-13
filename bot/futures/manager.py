# bot/futures/manager.py
import logging
from bot.futures.db import get_open_trades, update_trade_price
from bot.futures.risk import should_exit
from bot.futures.trader import close_trade

log = logging.getLogger(__name__)


def manage_futures_positions(client, db_path, current_prices: dict, sim=False):
    """Check every open trade against current prices and close if stop/target is hit.

    Parameters
    ----------
    client:         Tradovate API client (or MagicMock in tests/sim mode).
    db_path:        Path to the SQLite database.
    current_prices: Mapping of symbol -> latest market price.
    sim:            When True, skip live order submission.
    """
    open_trades = get_open_trades(db_path)
    if not open_trades:
        return

    for trade in open_trades:
        symbol = trade['symbol']
        current_price = current_prices.get(symbol)
        if current_price is None:
            log.warning('No price for %s — skipping', symbol)
            continue

        update_trade_price(db_path, trade['id'], current_price)

        reason = should_exit(
            trade['direction'],
            current_price,
            float(trade['stop_price']),
            float(trade['target_price']),
        )

        if reason:
            close_trade(client, db_path, trade, current_price, reason, sim=sim)
