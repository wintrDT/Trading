import time
from datetime import date, datetime, timezone
from bot.config import EXIT_RULES, FILL_WAIT_SECS
from bot.scanner import build_option_symbol, calc_dte
from bot.db import get_open_trades, update_trade_closed, update_trade_status

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


def manage_positions(client, db_path):
    today = date.today()
    positions = client.get_positions()
    pos_map = {str(p.symbol): float(p.mark_price) for p in positions}
    for trade in get_open_trades(db_path):
        if trade['status'] != 'open':
            continue
        dte = calc_dte(date.fromisoformat(trade['expiration']), today)
        current_mark = _get_spread_mark(pos_map, trade)
        reason = should_close(trade['entry_credit'], current_mark, dte)
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
