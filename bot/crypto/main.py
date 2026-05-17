# bot/crypto/main.py
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.crypto.config import CRYPTO_DB_PATH, SYMBOLS, RISK_RULES, STRATEGY_PARAMS, SYMBOL_OVERRIDES
from bot.crypto.db import (
    init_db, insert_signal, insert_trade, update_trade_price, update_trade_closed,
    mark_signal_traded, get_open_trades, get_daily_pnl, get_all_time_pnl, insert_snapshot,
    set_setting, get_setting, get_crypto_bias,
)
from bot.crypto.news import fetch_and_store_crypto_news
import pytz
from bot.crypto.price_feed import get_prices, get_price_history, get_latest_candles, get_5min_trends
# Reuse strategy primitives from the futures package
from bot.futures.strategy import (
    VWAPState, ChannelState, SMAState, RSIState,
    calc_vwap, check_vwap_signal, check_channel_signal, check_rsi_filter,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ET = pytz.timezone('America/New_York')

_vwap_states:    dict = {}
_channel_states: dict = {}
_sma_states:     dict = {}
_rsi_states:     dict = {}
_prev_prices:    dict = {}  # {symbol: last bar's close — used for momentum confirmation}
_peak_dev:       dict = {}  # {symbol: {'side','peak'}} — bounce-confirmed reversion state


def _check_reversion_entry(symbol, dev_pct, thresh_long, thresh_short, retrace=0.10):
    """Bounce-confirmed VWAP reversion (same pattern as futures).

    Tracks peak deviation while price extends. Returns a direction after either:
      - price has retraced `retrace` fraction back toward VWAP from the peak, OR
      - first crossing into the threshold band is already past 2x threshold (instant fire — already very stretched)
    """
    state = _peak_dev.get(symbol)

    # Stale state — price crossed VWAP, abandon pending entry
    if state and ((state['side'] == 'long'  and dev_pct >= 0) or
                  (state['side'] == 'short' and dev_pct <= 0)):
        _peak_dev.pop(symbol, None)
        state = None

    if dev_pct <= -thresh_long:
        # Instant-fire path: deviation already past 2x threshold = take the trade now, skip confirmation
        if abs(dev_pct) >= 2 * thresh_long:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion long FIRES (extreme dev=%.3f%%, threshold=%.2f%%)', symbol, dev_pct, thresh_long)
            return 'long'
        if state is None or state['side'] != 'long':
            _peak_dev[symbol] = {'side': 'long', 'peak': dev_pct}
            log.info('%s reversion long pending — peak=%.3f%% (waiting for %.0f%% retrace)', symbol, dev_pct, retrace * 100)
            return None
        if dev_pct < state['peak']:
            state['peak'] = dev_pct
            return None
        if abs(dev_pct - state['peak']) / abs(state['peak']) >= retrace:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion long FIRES (peak=%.3f%%, now=%.3f%%)', symbol, state['peak'], dev_pct)
            return 'long'
        return None

    if dev_pct >= thresh_short:
        if dev_pct >= 2 * thresh_short:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion short FIRES (extreme dev=%.3f%%, threshold=%.2f%%)', symbol, dev_pct, thresh_short)
            return 'short'
        if state is None or state['side'] != 'short':
            _peak_dev[symbol] = {'side': 'short', 'peak': dev_pct}
            log.info('%s reversion short pending — peak=%.3f%% (waiting for %.0f%% retrace)', symbol, dev_pct, retrace * 100)
            return None
        if dev_pct > state['peak']:
            state['peak'] = dev_pct
            return None
        if (state['peak'] - dev_pct) / state['peak'] >= retrace:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion short FIRES (peak=%.3f%%, now=%.3f%%)', symbol, state['peak'], dev_pct)
            return 'short'
        return None

    # Inside threshold band but state pending — fire if bounce already crossed retrace target
    if state:
        if state['side'] == 'long' and abs(dev_pct - state['peak']) / abs(state['peak']) >= retrace:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion long FIRES post-band (peak=%.3f%%, now=%.3f%%)', symbol, state['peak'], dev_pct)
            return 'long'
        if state['side'] == 'short' and (state['peak'] - dev_pct) / state['peak'] >= retrace:
            _peak_dev.pop(symbol, None)
            log.info('%s reversion short FIRES post-band (peak=%.3f%%, now=%.3f%%)', symbol, state['peak'], dev_pct)
            return 'short'

    return None

# 5-min trend cache — refresh every 5 minutes (no point fetching faster)
_trend_cache: dict = {}          # {symbol: trend}
_trend_fetched_at = None


def _is_active_hours() -> bool:
    """Block 1–7 AM ET — thin volume, high fakeout rate."""
    hour = datetime.now(ET).hour
    return not (1 <= hour < 7)


def _refresh_trends_if_stale():
    global _trend_cache, _trend_fetched_at
    now = datetime.now(timezone.utc)
    if _trend_fetched_at is None or (now - _trend_fetched_at).total_seconds() >= 300:
        try:
            _trend_cache     = get_5min_trends(SYMBOLS)
            _trend_fetched_at = now
            log.info('5-min trends refreshed: %s', _trend_cache)
        except Exception:
            log.exception('5-min trend refresh failed')


def _reset_state():
    global _vwap_states, _channel_states, _sma_states, _rsi_states, _peak_dev
    _vwap_states    = {s: VWAPState()    for s in SYMBOLS}
    _channel_states = {s: ChannelState() for s in SYMBOLS}
    _sma_states     = {s: SMAState()     for s in SYMBOLS}
    _rsi_states     = {s: RSIState()     for s in SYMBOLS}
    _peak_dev       = {}
    log.info('Crypto state reset')


def _warmup():
    for symbol in SYMBOLS:
        history = get_price_history(symbol, bars=30)
        if not history:
            log.warning('No history for %s', symbol)
            continue
        for price in history:
            _channel_states[symbol].update(price)
            _sma_states[symbol].update(price)
            _rsi_states[symbol].update(price)
            _vwap_states[symbol].add_bar(price=price, volume=1)
        log.info('%s warmed up with %d historical bars', symbol, len(history))


def _calc_stop_target(direction: str, entry: float, symbol: str, counter_trend: bool = False) -> tuple:
    overrides  = SYMBOL_OVERRIDES.get(symbol, {})
    stop_pct   = overrides.get('stop_pct',   RISK_RULES['stop_pct'])   / 100.0
    target_pct = overrides.get('target_pct', RISK_RULES['target_pct']) / 100.0
    if counter_trend:
        target_pct *= STRATEGY_PARAMS.get('counter_trend_target_mult', 0.5)
    if direction == 'long':
        stop   = entry * (1 - stop_pct)
        target = entry * (1 + target_pct)
    else:
        stop   = entry * (1 + stop_pct)
        target = entry * (1 - target_pct)
    return round(stop, 6), round(target, 6)


def _calc_pnl(direction: str, entry: float, close: float, size: float) -> float:
    points = close - entry if direction == 'long' else entry - close
    return round(points * size, 2)


def _maybe_breakeven(direction: str, entry: float, current: float, target: float) -> float | None:
    """Trail stop to entry once 60% of the way to target — reduces noise-triggered BE exits."""
    pct = STRATEGY_PARAMS['breakeven_trigger_pct']
    if direction == 'long':
        trigger = entry + (target - entry) * pct
        if current >= trigger:
            return entry
    else:
        trigger = entry - (entry - target) * pct
        if current <= trigger:
            return entry
    return None


def job_scan():
    _refresh_trends_if_stale()

    try:
        candles = get_latest_candles(SYMBOLS)
    except Exception:
        log.exception('Crypto candle fetch failed')
        return
    now_iso = datetime.now(timezone.utc).isoformat()

    # Persist latest prices for the dashboard
    for symbol, c in candles.items():
        try:
            set_setting(CRYPTO_DB_PATH, f'price_{symbol}', str(c['price']))
        except Exception:
            pass

    open_trades      = get_open_trades(CRYPTO_DB_PATH)
    open_trade_syms  = {t['symbol'] for t in open_trades}
    open_count       = len(open_trades)
    max_pos          = RISK_RULES['max_positions']

    for symbol in SYMBOLS:
        candle = candles.get(symbol)
        if candle is None:
            continue
        price  = candle['price']
        volume = candle['volume']

        vwap_state    = _vwap_states.setdefault(symbol,    VWAPState())
        channel_state = _channel_states.setdefault(symbol, ChannelState())
        sma_state     = _sma_states.setdefault(symbol,     SMAState())
        rsi_state     = _rsi_states.setdefault(symbol,     RSIState())

        channel_state.update(price)
        sma_state.update(price)
        rsi_state.update(price)
        vwap_state.add_bar(price=price, volume=volume)  # real volume now

        # Skip entry checks when already in a trade on this symbol or at max positions
        if symbol in open_trade_syms or open_count >= max_pos:
            continue

        rsi = rsi_state.value()
        signal, strategy = None, None
        dev_pct = None
        counter_trend = False

        vwap = calc_vwap(vwap_state)
        threshold = STRATEGY_PARAMS['vwap_deviation_pct']
        if vwap is not None:
            dev_pct = (price - vwap) / vwap * 100
            direction = _check_reversion_entry(symbol, dev_pct, threshold, threshold)
            if direction and check_rsi_filter(rsi, direction):
                signal, strategy = direction, 'vwap'

        if signal is None:
            sma = sma_state.value()
            ch_dir = check_channel_signal(price, channel_state, sma,
                                           min_width_pct=STRATEGY_PARAMS['channel_min_width_pct'])
            if ch_dir and check_rsi_filter(rsi, ch_dir):
                signal, strategy = ch_dir, 'channel'

        if signal is None:
            continue

        # Trend filter: block counter-trend signals UNLESS deviation is extreme (>= 2x threshold)
        # This catches short opportunities on pops even when overall trend is up
        trend_5m = _trend_cache.get(symbol)
        if trend_5m is not None and trend_5m != signal:
            extreme_dev = dev_pct is not None and abs(dev_pct) >= 2 * threshold
            if not extreme_dev:
                log.info('%s %s blocked by 5-min trend (%s) for %s — dev_pct=%s', strategy, signal, trend_5m, symbol,
                         f'{dev_pct:.3f}%' if dev_pct is not None else 'n/a')
                continue
            counter_trend = True
            log.info('%s %s counter-trend allowed (extreme dev=%.3f%%) for %s', strategy, signal, dev_pct, symbol)

        # Block if news bias is directly against the signal
        bias = get_crypto_bias(CRYPTO_DB_PATH, symbol)
        if bias is not None and bias != signal:
            log.info('Signal %s blocked by news bias (%s) for %s', signal, bias, symbol)
            continue

        signal_id = insert_signal(CRYPTO_DB_PATH, {
            'ts': now_iso, 'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'traded': 0,
        })

        size = round(RISK_RULES['position_size_usd'] / price, 6)
        stop, target = _calc_stop_target(signal, price, symbol, counter_trend=counter_trend)
        insert_trade(CRYPTO_DB_PATH, {
            'symbol':       symbol,
            'strategy':     strategy,
            'direction':    signal,
            'entry_price':  price,
            'entry_ts':     now_iso,
            'stop_price':   stop,
            'target_price': target,
            'size':         size,
            'status':       'open',
        })
        try:
            mark_signal_traded(CRYPTO_DB_PATH, signal_id)
        except ValueError:
            pass
        open_count += 1
        log.info('Entry: %s %s %s @ %.4f  stop=%.4f  target=%.4f  RSI=%.1f',
                 signal, symbol, strategy, price, stop, target, rsi or 0)


def job_manage():
    try:
        prices = get_prices(SYMBOLS)
    except Exception:
        log.exception('Crypto manager price fetch failed')
        return

    for trade in get_open_trades(CRYPTO_DB_PATH):
        symbol = trade['symbol']
        current = prices.get(symbol)
        if current is None:
            continue

        direction = trade['direction']
        entry     = float(trade['entry_price'])
        stop      = float(trade['stop_price'])
        target    = float(trade['target_price'])
        size      = float(trade['size'])

        # Trail stop to breakeven once 25% of target reached
        be = _maybe_breakeven(direction, entry, current, target)
        if be is not None:
            if (direction == 'long' and be > stop) or (direction == 'short' and be < stop):
                stop = be
                update_trade_price(CRYPTO_DB_PATH, trade['id'], current, new_stop=stop)
                log.info('Trailed stop to BE for %s id=%s', symbol, trade['id'])
            else:
                update_trade_price(CRYPTO_DB_PATH, trade['id'], current)
        else:
            update_trade_price(CRYPTO_DB_PATH, trade['id'], current)

        reason = None
        if direction == 'long':
            if current <= stop:     reason = 'stop_loss'
            elif current >= target: reason = 'profit_target'
        else:
            if current >= stop:     reason = 'stop_loss'
            elif current <= target: reason = 'profit_target'

        # Time-based exit — close stale trades after configured timeout
        if reason is None:
            try:
                entry_dt  = datetime.fromisoformat(trade['entry_ts'].replace('Z', '+00:00'))
                age_min   = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
                if age_min >= RISK_RULES['trade_timeout_minutes']:
                    reason = 'timeout'
            except Exception:
                pass

        if reason:
            pnl = _calc_pnl(direction, entry, current, size)
            update_trade_closed(
                CRYPTO_DB_PATH, trade['id'],
                close_price=current, close_reason=reason,
                close_ts=datetime.now(timezone.utc).isoformat(), pnl=pnl,
            )
            log.info('Closed: %s %s reason=%s price=%.4f pnl=%.2f', direction, symbol, reason, current, pnl)


def job_news():
    try:
        token = get_setting(CRYPTO_DB_PATH, 'cryptopanic_token', '')
        fetch_and_store_crypto_news(CRYPTO_DB_PATH, token)
    except Exception:
        log.exception('Crypto news fetch error')


def job_snapshot():
    try:
        today    = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        realized = get_daily_pnl(CRYPTO_DB_PATH, today)
        equity   = 10000.0 + get_all_time_pnl(CRYPTO_DB_PATH)
        insert_snapshot(CRYPTO_DB_PATH, {
            'ts':                 datetime.now(timezone.utc).isoformat(),
            'equity':             equity,
            'realized_pnl_today': realized,
        })
    except Exception:
        log.exception('Snapshot error')


def main():
    init_db(CRYPTO_DB_PATH)
    _reset_state()
    _warmup()

    scheduler = BlockingScheduler(timezone=timezone.utc)
    scheduler.add_job(job_scan,     IntervalTrigger(seconds=15), id='scan')
    scheduler.add_job(job_manage,   IntervalTrigger(seconds=10), id='manage')
    scheduler.add_job(job_snapshot, IntervalTrigger(minutes=5),  id='snapshot')
    scheduler.add_job(job_news,     IntervalTrigger(minutes=2),  id='news')
    job_news()  # fetch immediately on startup

    log.info('Crypto bot running [SIM]. Ctrl+C to stop.')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Shutting down.')
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
