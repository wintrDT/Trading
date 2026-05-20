# bot/futures/trader.py
import logging
from datetime import datetime, timezone
from bot.futures.config import TICK_INFO, RISK_RULES, SYMBOL_RISK, TOPSTEP_RULES
from bot.futures.risk import calc_stop_price, calc_target_price, calc_pnl
from bot.futures.db import insert_trade, update_trade_closed, mark_signal_traded, get_open_trades, get_last_close_info
from bot.futures import notifier

log = logging.getLogger(__name__)


def place_entry(client, db_path, signal, contracts, sim=False):
    symbol    = signal['symbol']
    direction = signal['direction']
    price     = float(signal['price'])
    signal_id = signal.get('signal_id')

    # Hard cap contracts at the TopStep safe limit, regardless of conviction-based sizing
    max_allowed = min(TOPSTEP_RULES.get('max_contracts', 2), RISK_RULES.get('max_contracts', 2))
    if contracts > max_allowed:
        log.info('Capping %s entry contracts %d -> %d (TopStep limit)', symbol, contracts, max_allowed)
        contracts = max_allowed

    if any(t['symbol'] == symbol for t in get_open_trades(db_path)):
        log.info('Skipping %s %s — already have open trade (bot DB)', symbol, direction)
        return None

    # Broker reconciliation — also check TopStep's ACTUAL positions, not just the
    # bot DB. Prevents stacking contracts on top of a position the bot lost track
    # of (this is what produced the 4-contract orphan). Only for live TopStep.
    if not sim and hasattr(client, 'get_open_position'):
        try:
            broker_pos = client.get_open_position(symbol)
            if broker_pos and broker_pos.get('size', 0) != 0:
                log.warning('Skipping %s %s — TopStep already holds %d %s contracts (DB out of sync)',
                            symbol, direction, broker_pos['size'], broker_pos['side'])
                return None
        except Exception:
            log.exception('Broker position check failed for %s — skipping entry to be safe', symbol)
            return None

    cooldown = RISK_RULES.get('cooldown_minutes', 0)
    if cooldown:
        last_close, last_reason = get_last_close_info(db_path, symbol)
        if last_close:
            # 3x cooldown after stop_loss; 2x more if we're near a news event
            effective_cooldown = cooldown * 3 if last_reason == 'stop_loss' else cooldown
            if signal.get('near_event'):
                effective_cooldown *= 2
            last_dt = datetime.fromisoformat(last_close.replace('Z', '+00:00'))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            if elapsed < effective_cooldown:
                suffix = ' (post-stop)' if last_reason == 'stop_loss' else ''
                if signal.get('near_event'): suffix += ' (near event)'
                log.info('Skipping %s — cooldown %.1f min remaining%s',
                         symbol, effective_cooldown - elapsed, suffix)
                return None

    tick_data    = TICK_INFO.get(symbol, TICK_INFO['ES'])
    tick         = tick_data['tick']
    risk         = SYMBOL_RISK.get(symbol, RISK_RULES)
    counter_trend = direction == 'short' and signal.get('trend') == 'up'
    if direction == 'short':
        stop_ticks   = risk.get('short_stop_ticks', risk['stop_ticks'])
        target_ticks = risk.get('counter_trend_target_ticks', 16) if counter_trend else risk.get('short_target_ticks', risk['target_ticks'])
    else:
        stop_ticks   = risk['stop_ticks']
        target_ticks = risk['target_ticks']

    # ATR-adaptive stop: 2.5x average price move per bar, clamped to [0.5x, 1.5x]
    # of the fixed-tick stop. Tightened from 2.5x ceiling on 2026-05-19 after
    # evening live trades stopped out at -$200/-$300 (wide ATR stops in volatile
    # transition window). Smaller cap = smaller losses per stop-out.
    atr = signal.get('atr')
    if atr is not None and atr > 0:
        atr_stop_ticks = round(atr * 2.5 / tick)
        floor_ticks    = max(4, stop_ticks // 2)
        ceiling_ticks  = int(stop_ticks * 1.5)  # was 2.5x, now 1.5x
        stop_ticks     = max(floor_ticks, min(ceiling_ticks, atr_stop_ticks))

    # News volatility engine — widen stop, tighten target when near a scheduled event
    if signal.get('near_event'):
        stop_ticks   = int(stop_ticks * 1.25)   # 25% wider stop (survive vol spike)
        target_ticks = max(4, int(target_ticks * 0.75))  # 25% closer target (take profit faster)

    # Place the order FIRST (live), then read the ACTUAL fill price from TopStep
    # so the recorded entry — and the stop/target derived from it — match reality
    # instead of the signal-time quote. Sim mode keeps using the signal price.
    order_id   = 'SIM'
    entry_price = price
    if not sim:
        action = 'Buy' if direction == 'long' else 'Sell'
        try:
            resp     = client.place_order(symbol, action, contracts)
            order_id = str(resp.get('orderId', 'UNKNOWN'))
        except Exception:
            log.exception('Broker order FAILED for %s %s — no trade recorded', direction, symbol)
            notifier.notify_system(f'Order FAILED: {direction} {symbol} — check TopStep manually', level='error')
            return None
        # Read the real fill price from the resulting position (averagePrice).
        # Retry a few times — market orders fill fast but the position may take
        # a moment to register.
        if hasattr(client, 'get_open_position'):
            import time as _time
            for _ in range(4):
                _time.sleep(0.3)
                try:
                    pos = client.get_open_position(symbol)
                except Exception:
                    pos = None
                if pos and pos.get('avgPrice'):
                    entry_price = float(pos['avgPrice'])
                    break
            if entry_price != price:
                log.info('%s actual fill %.2f (signal was %.2f, slip %.2f)',
                         symbol, entry_price, price, entry_price - price)

    # Stop + target derived from the ACTUAL entry price
    stop_price   = calc_stop_price(direction, entry_price, stop_ticks, tick)
    vwap_val     = signal.get('vwap')
    target_price = calc_target_price(direction, entry_price, target_ticks, tick)
    if vwap_val and vwap_val > 0:
        snapped     = round(round(vwap_val / tick) * tick, 4)
        stop_dist   = abs(stop_price - entry_price)
        target_dist = abs(snapped - entry_price)
        valid = (direction == 'long'  and snapped > entry_price) or (direction == 'short' and snapped < entry_price)
        if valid and target_dist >= stop_dist:
            target_price = snapped

    trade_id = insert_trade(db_path, {
        'symbol':        symbol,
        'strategy':      signal['strategy'],
        'direction':     direction,
        'entry_price':   entry_price,
        'entry_ts':      datetime.now(timezone.utc).isoformat(),
        'stop_price':    stop_price,
        'target_price':  target_price,
        'contracts':     contracts,
        'order_id':      order_id,
        'status':        'open',
        'entry_rsi':     signal.get('entry_rsi'),
        'entry_dev_pct': signal.get('entry_dev_pct'),
    })
    if signal_id:
        try:
            mark_signal_traded(db_path, signal_id)
        except ValueError:
            pass
    stop_distance_ticks = round(abs(entry_price - stop_price) / tick)
    log.info('Entry: %s %s %s @ %.2f stop=%.2f (%d ticks, ATR=%s) target=%.2f%s',
             direction, symbol, signal['strategy'], entry_price, stop_price,
             stop_distance_ticks,
             f'{atr:.3f}' if atr else 'n/a',
             target_price,
             ' [SIM]' if sim else '')
    notifier.notify_entry(symbol, direction, entry_price, stop_price, target_price, contracts, signal['strategy'])
    return trade_id


def close_trade(client, db_path, trade, current_price, reason, sim=False):
    tick_data   = TICK_INFO.get(trade['symbol'], TICK_INFO['ES'])
    point_value = tick_data['point_value']
    pnl         = calc_pnl(trade['direction'], trade['entry_price'], current_price,
                           trade['contracts'], point_value)

    if not sim and trade.get('order_id') not in ('SIM', 'UNKNOWN', None):
        # Prefer flatten (closeContract) — guarantees the position is fully closed
        # at the broker even if DB size != broker size. Falls back to a netting
        # market order for clients that don't expose close_position (e.g. Tastytrade).
        try:
            if hasattr(client, 'close_position'):
                client.close_position(trade['symbol'])
            else:
                action = 'Sell' if trade['direction'] == 'long' else 'Buy'
                client.place_order(trade['symbol'], action, trade['contracts'])
        except Exception:
            log.exception('Failed to close %s trade id=%s at broker — leaving OPEN for retry',
                          trade['symbol'], trade['id'])
            return  # do NOT mark closed — manager will retry next cycle

    try:
        update_trade_closed(
            db_path, trade['id'],
            close_price=current_price,
            close_reason=reason,
            close_ts=datetime.now(timezone.utc).isoformat(),
            pnl=pnl,
        )
    except ValueError:
        # Already closed (race with web manual-close) — broker is flat, nothing to do
        log.info('Trade id=%s already closed — skipping DB update', trade['id'])
        return

    log.info('Closed: %s %s reason=%s price=%.2f pnl=%.2f',
             trade['direction'], trade['symbol'], reason, current_price, pnl)
    notifier.notify_exit(trade['symbol'], trade['direction'], pnl, reason,
                         float(trade['entry_price']), float(current_price))
