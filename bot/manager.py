import time
from datetime import date, datetime, timezone
import pytz
from bot.config import EXIT_RULES, ZERO_DTE_EXIT_RULES, FILL_WAIT_SECS, TIMEZONE
from bot.scanner import build_option_symbol, calc_dte
from bot.db import get_open_trades, update_trade_closed, update_trade_status, update_trade_mark

_ET = pytz.timezone(TIMEZONE)

_FILL_STATUSES = {'filled', 'partially_filled'}


def calc_pnl_pct(entry_credit: float, current_mark: float) -> float:
    if entry_credit == 0:
        return 0.0
    return round((entry_credit - current_mark) / entry_credit * 100, 2)


def should_close(entry_credit: float, current_mark: float, dte: int = 99):
    pnl_pct = calc_pnl_pct(entry_credit, current_mark)
    if pnl_pct >= EXIT_RULES['profit_target_pct']:
        return 'profit_target'
    if pnl_pct <= -EXIT_RULES['stop_loss_pct']:
        return 'stop_loss'
    if dte <= EXIT_RULES['dte_close']:
        return 'dte_expire'
    return None


def should_close_0dte(entry_credit: float, current_mark: float) -> str | None:
    pnl_pct = calc_pnl_pct(entry_credit, current_mark)
    if pnl_pct >= ZERO_DTE_EXIT_RULES['profit_target_pct']:
        return 'profit_target'
    if pnl_pct <= -ZERO_DTE_EXIT_RULES['stop_loss_pct']:
        return 'stop_loss'
    h, m = map(int, ZERO_DTE_EXIT_RULES['latest_close_time'].split(':'))
    from datetime import time as _Time
    if datetime.now(_ET).time() >= _Time(h, m):
        return 'time_exit'
    return None


def _get_spread_mark(pos_map, trade):
    # mark_price is per-share (e.g. 1.23 = $1.23/share = $123/contract)
    # entry_credit in db is also per-share — units match
    underlying = trade['underlying']
    exp = date.fromisoformat(trade['expiration'])

    def mark(opt_type, strike):
        sym = build_option_symbol(underlying, exp, opt_type, strike)
        return pos_map.get(sym, 0.0)

    cost_to_close = mark('P', trade['short_put_strike']) - mark('P', trade['long_put_strike'])
    if trade['strategy'] == 'iron_condor':
        cost_to_close += (mark('C', trade['short_call_strike'])
                          - mark('C', trade['long_call_strike']))
    return cost_to_close


def _build_close_legs(trade):
    underlying = trade['underlying']
    exp = date.fromisoformat(trade['expiration'])
    n = trade['contracts']
    legs = [
        {'symbol': build_option_symbol(underlying, exp, 'P', trade['short_put_strike']),
         'quantity': n, 'action': 'BUY_TO_CLOSE'},
        {'symbol': build_option_symbol(underlying, exp, 'P', trade['long_put_strike']),
         'quantity': n, 'action': 'SELL_TO_CLOSE'},
    ]
    if trade['strategy'] == 'iron_condor':
        legs += [
            {'symbol': build_option_symbol(underlying, exp, 'C', trade['short_call_strike']),
             'quantity': n, 'action': 'BUY_TO_CLOSE'},
            {'symbol': build_option_symbol(underlying, exp, 'C', trade['long_call_strike']),
             'quantity': n, 'action': 'SELL_TO_CLOSE'},
        ]
    return legs


def _exit_reason(trade, current_mark, dte):
    if trade.get('trade_type') == '0dte':
        return should_close_0dte(trade['entry_credit'], current_mark)
    return should_close(trade['entry_credit'], current_mark, dte)


def manage_positions(client, db_path):
    today = date.today()
    open_trades = [t for t in get_open_trades(db_path) if t['status'] == 'open']
    if not open_trades:
        return

    real_trades = [t for t in open_trades if t.get('order_id') != 'SIM']
    sim_trades  = [t for t in open_trades if t.get('order_id') == 'SIM']

    # ── Real trades ───────────────────────────────────────────────────
    if real_trades:
        positions = client.get_positions()
        pos_map = {str(p.symbol): float(p.mark_price) for p in positions}
        for trade in real_trades:
            dte = calc_dte(date.fromisoformat(trade['expiration']), today)
            current_mark = _get_spread_mark(pos_map, trade)
            update_trade_mark(db_path, trade['id'], current_mark)
            reason = _exit_reason(trade, current_mark, dte)
            if reason is None:
                continue

            legs = _build_close_legs(trade)
            response = client.place_debit_order(legs, round(current_mark, 2))
            order_id = str(response.order.id)
            update_trade_status(db_path, trade['id'], 'pending')

            time.sleep(FILL_WAIT_SECS)
            order = client.get_order(order_id)
            if str(order.status).lower() in _FILL_STATUSES:
                update_trade_closed(
                    db_path, trade['id'],
                    close_credit=current_mark,
                    close_reason=reason,
                    close_ts=datetime.now(timezone.utc).isoformat(),
                )
            else:
                try:
                    client.cancel_order(order_id)
                except Exception:
                    pass
                update_trade_status(db_path, trade['id'], 'open')

    # ── Sim trades ────────────────────────────────────────────────────
    if sim_trades:
        marks = client.get_sim_marks(sim_trades)
        for trade in sim_trades:
            dte = calc_dte(date.fromisoformat(trade['expiration']), today)
            current_mark = marks.get(trade['id'])
            if current_mark is None:
                is_0dte = trade.get('trade_type') == '0dte'
                reason = _exit_reason(trade, 0.0, dte) if is_0dte else (
                    'dte_expire' if dte <= EXIT_RULES['dte_close'] else None
                )
                current_mark = 0.0
            else:
                update_trade_mark(db_path, trade['id'], current_mark)
                reason = _exit_reason(trade, current_mark, dte)
            if reason is None:
                continue
            update_trade_closed(
                db_path, trade['id'],
                close_credit=current_mark,
                close_reason=reason,
                close_ts=datetime.now(timezone.utc).isoformat(),
            )
