# bot/futures/trader.py
import logging
from datetime import datetime, timezone
from bot.futures.config import TICK_INFO, RISK_RULES
from bot.futures.risk import calc_stop_price, calc_target_price, calc_pnl
from bot.futures.db import insert_trade, update_trade_closed, mark_signal_traded, get_open_trades

log = logging.getLogger(__name__)


def place_entry(client, db_path, signal, contracts, sim=False):
    symbol    = signal['symbol']
    direction = signal['direction']
    price     = float(signal['price'])
    signal_id = signal.get('signal_id')

    if any(t['symbol'] == symbol for t in get_open_trades(db_path)):
        log.info('Skipping %s %s — already have open trade', symbol, direction)
        return None

    tick_data    = TICK_INFO.get(symbol, TICK_INFO['ES'])
    tick         = tick_data['tick']
    stop_price   = calc_stop_price(direction, price, RISK_RULES['stop_ticks'], tick)
    target_price = calc_target_price(direction, price, RISK_RULES['target_ticks'], tick)

    order_id = 'SIM'
    if not sim:
        action   = 'Buy' if direction == 'long' else 'Sell'
        resp     = client.place_order(symbol, action, contracts)
        order_id = str(resp.get('orderId', 'UNKNOWN'))

    trade_id = insert_trade(db_path, {
        'symbol':       symbol,
        'strategy':     signal['strategy'],
        'direction':    direction,
        'entry_price':  price,
        'entry_ts':     datetime.now(timezone.utc).isoformat(),
        'stop_price':   stop_price,
        'target_price': target_price,
        'contracts':    contracts,
        'order_id':     order_id,
        'status':       'open',
    })
    if signal_id:
        try:
            mark_signal_traded(db_path, signal_id)
        except ValueError:
            pass
    log.info('Entry: %s %s %s @ %.2f stop=%.2f target=%.2f%s',
             direction, symbol, signal['strategy'], price, stop_price, target_price,
             ' [SIM]' if sim else '')
    return trade_id


def close_trade(client, db_path, trade, current_price, reason, sim=False):
    tick_data   = TICK_INFO.get(trade['symbol'], TICK_INFO['ES'])
    point_value = tick_data['point_value']
    pnl         = calc_pnl(trade['direction'], trade['entry_price'], current_price,
                           trade['contracts'], point_value)
    if not sim and trade.get('order_id') != 'SIM':
        action = 'Sell' if trade['direction'] == 'long' else 'Buy'
        try:
            client.place_order(trade['symbol'], action, trade['contracts'])
        except Exception:
            log.exception('Failed to close %s trade id=%s', trade['symbol'], trade['id'])
            return
    update_trade_closed(
        db_path, trade['id'],
        close_price=current_price,
        close_reason=reason,
        close_ts=datetime.now(timezone.utc).isoformat(),
        pnl=pnl,
    )
    log.info('Closed: %s %s reason=%s price=%.2f pnl=%.2f',
             trade['direction'], trade['symbol'], reason, current_price, pnl)
