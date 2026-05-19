# bot/futures/manager.py
import logging
from datetime import datetime, timezone
from bot.futures.config import RISK_RULES, TICK_INFO
from bot.futures.db import get_open_trades, update_trade_price, update_trade_extremes
from bot.futures.risk import should_exit, calc_breakeven_stop, calc_trailing_stop
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

        stop      = float(trade['stop_price'])
        entry     = float(trade['entry_price'])
        target    = float(trade['target_price'])
        direction = trade['direction']
        tick      = TICK_INFO.get(symbol, TICK_INFO['ES'])['tick']
        pv        = TICK_INFO.get(symbol, TICK_INFO['ES'])['point_value']

        # MAE/MFE: track worst and best unrealized P&L the trade ever reached
        unrealized = (current_price - entry if direction == 'long' else entry - current_price) * trade['contracts'] * pv
        prev_fav = trade.get('max_favorable')
        prev_adv = trade.get('max_adverse')
        new_fav = unrealized if prev_fav is None else max(prev_fav, unrealized)
        new_adv = unrealized if prev_adv is None else min(prev_adv, unrealized)
        if new_fav != prev_fav or new_adv != prev_adv:
            update_trade_extremes(db_path, trade['id'], new_fav, new_adv)

        # Best stop = highest of original, breakeven, and trailing (for longs; lowest for shorts)
        be_stop   = calc_breakeven_stop(direction, entry, current_price, target)
        trail_stop = calc_trailing_stop(direction, entry, current_price, tick)

        new_stop = stop
        for candidate in [be_stop, trail_stop]:
            if candidate is None:
                continue
            if direction == 'long'  and candidate > new_stop:
                new_stop = candidate
            elif direction == 'short' and candidate < new_stop:
                new_stop = candidate

        if new_stop != stop:
            stop = new_stop
            trade = {**trade, 'stop_price': stop}
            update_trade_price(db_path, trade['id'], current_price, new_stop=stop)
            log.info('Stop trailed to %.2f for %s trade id=%s', stop, symbol, trade['id'])
        else:
            update_trade_price(db_path, trade['id'], current_price)

        reason = should_exit(trade['direction'], current_price, stop, target)

        if reason is None:
            try:
                entry_dt    = datetime.fromisoformat(trade['entry_ts'].replace('Z', '+00:00'))
                age_seconds = (datetime.now(timezone.utc) - entry_dt).total_seconds()
                age_min     = age_seconds / 60

                # Fast-fail: if trade hasn't shown positive MFE within the grace period
                # and is currently meaningfully underwater, exit early instead of grinding
                # to the full stop. Cuts the bleed from "instant losers" (trades that
                # never go green) — yesterday's audit showed ~19% of trades fit this.
                fast_fail_age = RISK_RULES.get('fast_fail_min_age_sec', 0)
                fast_fail_neg = RISK_RULES.get('fast_fail_max_neg_usd', 0)
                if (fast_fail_age
                        and age_seconds >= fast_fail_age
                        and (new_fav or 0) <= 0
                        and unrealized < fast_fail_neg):
                    reason = 'fast_fail'
                    log.info('Fast-fail closing %s %s id=%s — age=%.0fs MFE=$%.0f unrealized=$%.0f',
                             symbol, direction, trade['id'], age_seconds, new_fav or 0, unrealized)
                elif age_min >= RISK_RULES['trade_timeout_minutes']:
                    reason = 'timeout'
            except Exception:
                pass

        if reason:
            if reason == 'stop_loss':
                exit_price = stop
            elif reason == 'profit_target':
                exit_price = target
            else:
                exit_price = current_price
            close_trade(client, db_path, trade, exit_price, reason, sim=sim)
