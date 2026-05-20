import math
import time
from datetime import date, datetime, timezone
from bot.config import POSITION_SIZING, FILL_WAIT_SECS
from bot.scanner import build_option_symbol
from bot.db import (
    insert_trade, update_trade_status, update_trade_order_id,
    mark_scan_traded, get_open_trades,
)

_FILL_STATUSES = {'filled', 'partially_filled'}


def calc_contracts(net_liq: float, width: float) -> int:
    max_risk = net_liq * POSITION_SIZING['max_pct_per_trade']
    return math.floor(max_risk / (width * 100))


def build_bps_legs(underlying, expiration, short_put, long_put, contracts):
    exp = expiration if isinstance(expiration, date) else date.fromisoformat(expiration)
    return [
        {'symbol': build_option_symbol(underlying, exp, 'P', short_put),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'P', long_put),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
    ]


def build_ic_legs(underlying, expiration, short_put, long_put, short_call, long_call, contracts):
    exp = expiration if isinstance(expiration, date) else date.fromisoformat(expiration)
    return [
        {'symbol': build_option_symbol(underlying, exp, 'P', short_put),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'P', long_put),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'C', short_call),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'C', long_call),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
    ]


def _is_filled(client, order_id):
    order = client.get_order(order_id)
    return str(order.status).lower() in _FILL_STATUSES


def place_spread(client, db_path, setup, scan_id, net_liq, sim=False):
    underlying = setup['underlying']

    if any(t['underlying'] == underlying for t in get_open_trades(db_path)):
        return None

    contracts = calc_contracts(net_liq, setup['width'])
    if contracts < 1:
        return None

    if sim:
        trade_id = insert_trade(db_path, {
            'scan_id': scan_id,
            'underlying': underlying,
            'strategy': setup['strategy'],
            'expiration': setup['expiration'],
            'short_put_strike': setup['short_put_strike'],
            'long_put_strike': setup['long_put_strike'],
            'short_call_strike': setup['short_call_strike'],
            'long_call_strike': setup['long_call_strike'],
            'entry_credit': setup['credit'],
            'entry_ts': datetime.now(timezone.utc).isoformat(),
            'contracts': contracts,
            'order_id': 'SIM',
            'trade_type': setup.get('trade_type', 'premium'),
        })
        mark_scan_traded(db_path, scan_id)
        return trade_id

    if setup['strategy'] == 'bull_put_spread':
        legs = build_bps_legs(
            underlying, setup['expiration'],
            setup['short_put_strike'], setup['long_put_strike'],
            contracts,
        )
    else:
        legs = build_ic_legs(
            underlying, setup['expiration'],
            setup['short_put_strike'], setup['long_put_strike'],
            setup['short_call_strike'], setup['long_call_strike'],
            contracts,
        )

    credit = setup['credit']
    response = client.place_order(legs, credit)
    order_id = str(response.order.id)
    try:
        trade_id = insert_trade(db_path, {
            'scan_id': scan_id,
            'underlying': underlying,
            'strategy': setup['strategy'],
            'expiration': setup['expiration'],
            'short_put_strike': setup['short_put_strike'],
            'long_put_strike': setup['long_put_strike'],
            'short_call_strike': setup['short_call_strike'],
            'long_call_strike': setup['long_call_strike'],
            'entry_credit': credit,
            'entry_ts': datetime.now(timezone.utc).isoformat(),
            'contracts': contracts,
            'order_id': order_id,
            'trade_type': setup.get('trade_type', 'premium'),
        })
        update_trade_status(db_path, trade_id, 'pending')
    except Exception:
        client.cancel_order(order_id)
        raise

    time.sleep(FILL_WAIT_SECS)
    if _is_filled(client, order_id):
        update_trade_status(db_path, trade_id, 'open')
        mark_scan_traded(db_path, scan_id)
        return trade_id

    # Reprice 10% lower (more aggressive credit) and retry once
    try:
        client.cancel_order(order_id)
    except Exception:
        pass
    natural_credit = round(credit * 0.90, 2)
    response2 = client.place_order(legs, natural_credit)
    order_id2 = str(response2.order.id)
    update_trade_order_id(db_path, trade_id, order_id2)

    time.sleep(FILL_WAIT_SECS)
    if _is_filled(client, order_id2):
        update_trade_status(db_path, trade_id, 'open')
        mark_scan_traded(db_path, scan_id)
        return trade_id

    client.cancel_order(order_id2)
    update_trade_status(db_path, trade_id, 'cancelled')
    return None
