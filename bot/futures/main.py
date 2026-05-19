# bot/futures/main.py
import logging
from datetime import datetime
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from bot.futures.config import (
    FUTURES_DB_PATH, SYMBOLS, TICK_INFO, STRATEGY_PARAMS, RISK_RULES,
    TIMEZONE, MARKET_CLOSE_HOUR, MARKET_OPEN_HOUR, ORB_START, ORB_END,
    AV_API_KEY, SYMBOL_VWAP_PCT, SYMBOL_NEWS_KEYWORDS, BLOCKED_HOURS_ET,
    TOPSTEP_RULES,
)
from bot.futures.db import (
    init_db, insert_signal, get_daily_pnl, get_setting, set_setting,
    insert_snapshot, get_today_event_times,
)
from bot.futures.news import fetch_and_store_news
from bot.futures.tradovate_client import TradovateClient
from bot.futures.price_feed import get_prices as get_yf_prices, get_price_history
from bot.tt_client import TastytradeClient as _TastyClient
from bot.futures.strategy import (
    VWAPState, ORBState, ChannelState, SMAState, RSIState, VolatilityState,
    calc_vwap, check_vwap_signal, check_orb_signal, check_channel_signal, check_rsi_filter,
    classify_day_type, day_type_blocks_direction, compute_confidence,
    micro_momentum_blocks,
)
from bot.futures.risk import is_daily_loss_limit_hit
from bot.futures.db import get_market_bias
from bot.futures.trader import place_entry
from bot.futures.manager import manage_futures_positions
from bot.futures.tuner import run_tuner
from bot.futures import notifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ET = pytz.timezone(TIMEZONE)

_vwap_states:    dict = {}
_orb_states:     dict = {}
_channel_states: dict = {}
_sma_states:     dict = {}
_rsi_states:     dict = {}
_vol_states:     dict = {}
_peak_dev:       dict = {}   # symbol -> {'side': 'long'|'short', 'peak': dev_pct} — reversion confirmation
_day_type_cache: dict = {}   # symbol -> classified day type, cleared on daily reset
_last_price:     dict = {}   # symbol -> last scanned price (used for EOD session-close snapshot)
_daily_loss_notified_date:   str = ''  # 'YYYY-MM-DD' — prevents duplicate daily-loss Telegram pings
_daily_profit_notified_date: str = ''  # 'YYYY-MM-DD' — prevents duplicate profit-target Telegram pings


def _check_reversion_entry(symbol, dev_pct, thresh_long, thresh_short, retrace=0.25):
    """VWAP reversion with bounce confirmation.

    Tracks peak deviation while price is extended. Returns a direction only after
    price has retraced `retrace` fraction back toward VWAP from the peak.
    Avoids entering while a move is still accelerating away from VWAP.
    """
    state = _peak_dev.get(symbol)

    # Stale state — price crossed VWAP, abandon pending entry
    if state and ((state['side'] == 'long'  and dev_pct >= 0) or
                  (state['side'] == 'short' and dev_pct <= 0)):
        _peak_dev.pop(symbol, None)
        state = None

    if dev_pct <= -thresh_long:
        if state is None or state['side'] != 'long':
            _peak_dev[symbol] = {'side': 'long', 'peak': dev_pct}
            return None
        if dev_pct < state['peak']:
            state['peak'] = dev_pct  # still extending — update peak, wait
            return None
        if abs(dev_pct - state['peak']) / abs(state['peak']) >= retrace:
            _peak_dev.pop(symbol, None)
            return 'long'
        return None

    if dev_pct >= thresh_short:
        if state is None or state['side'] != 'short':
            _peak_dev[symbol] = {'side': 'short', 'peak': dev_pct}
            return None
        if dev_pct > state['peak']:
            state['peak'] = dev_pct
            return None
        if (state['peak'] - dev_pct) / state['peak'] >= retrace:
            _peak_dev.pop(symbol, None)
            return 'short'
        return None

    # Inside threshold band but state pending — fire if bounce already crossed retrace target
    if state:
        if state['side'] == 'long' and abs(dev_pct - state['peak']) / abs(state['peak']) >= retrace:
            _peak_dev.pop(symbol, None)
            return 'long'
        if state['side'] == 'short' and (state['peak'] - dev_pct) / state['peak'] >= retrace:
            _peak_dev.pop(symbol, None)
            return 'short'

    return None


def _rsi_momentum_ok(rsi: float | None, direction: str) -> bool:
    """Momentum RSI filter: confirm direction, only block extreme exhaustion."""
    if rsi is None:
        return True
    if direction == 'long':
        return rsi > 40          # needs upward momentum; block weak/bearish RSI
    if direction == 'short':
        return rsi < 60          # needs downward momentum; block strong/bullish RSI
    return True


def _is_market_hours(has_realtime: bool = False):
    now = datetime.now(ET).time()
    # Only skip the 5–6 PM ET maintenance window
    return not (_Time(17, 0) <= now < _Time(18, 0))


def _is_orb_period():
    now = datetime.now(ET).time()
    sh, sm = map(int, ORB_START.split(':'))
    eh, em = map(int, ORB_END.split(':'))
    return _Time(sh, sm) <= now < _Time(eh, em)


def _orb_start_minute():
    h, m = map(int, ORB_START.split(':'))
    return h * 60 + m


def _orb_end_minute():
    h, m = map(int, ORB_END.split(':'))
    return h * 60 + m


def _now_minute():
    now = datetime.now(ET)
    return now.hour * 60 + now.minute


def _reset_daily_state():
    global _vwap_states, _orb_states, _channel_states, _sma_states, _rsi_states, _vol_states, _peak_dev, _day_type_cache
    _vwap_states    = {s: VWAPState()        for s in SYMBOLS}
    _orb_states     = {s: ORBState()         for s in SYMBOLS}
    _channel_states = {s: ChannelState()     for s in SYMBOLS}
    _sma_states     = {s: SMAState()         for s in SYMBOLS}
    _rsi_states     = {s: RSIState()         for s in SYMBOLS}
    _vol_states     = {s: VolatilityState()  for s in SYMBOLS}
    _peak_dev       = {}
    _day_type_cache = {}
    log.info('Daily state reset — VWAP, ORB, channel, SMA, RSI, volatility, day-type, and pending entries cleared')


def _record_session_close():
    """Snapshot the last seen price for each symbol — used as 'prev_close' input
    to next session's day-type classifier. Runs at 16:58 ET (just before TopStep
    cutoff and the 17:00 maintenance close)."""
    for symbol in SYMBOLS:
        price = _last_price.get(symbol)
        if price:
            set_setting(FUTURES_DB_PATH, f'prev_close_{symbol}', str(price))
            log.info('Recorded session close for %s: %.2f', symbol, price)


def _warmup_state():
    """Pre-load today's intraday bars into all states so strategies fire immediately after restart."""
    import yfinance as yf
    from bot.futures.price_feed import _YF_MAP
    today = datetime.now(ET).strftime('%Y-%m-%d')
    for symbol in SYMBOLS:
        ticker = _YF_MAP.get(symbol)
        history = []
        if ticker:
            try:
                hist = yf.Ticker(ticker).history(period='1d', interval='1m')
                if not hist.empty:
                    history = [float(p) for p in hist['Close'].tolist() if not __import__('math').isnan(p)]
            except Exception:
                pass
        if not history:
            history = get_price_history(symbol, bars=30)
        if not history:
            log.warning('No history available for %s', symbol)
            continue
        for price in history:
            _channel_states[symbol].update(price)
            _sma_states[symbol].update(price)
            _rsi_states[symbol].update(price)
            _vol_states[symbol].update(price)
            _vwap_states[symbol].add_bar(price=price, volume=1)
        log.info('%s warmed up with %d intraday bars', symbol, len(history))


def job_scan(client):
    if not _is_market_hours(has_realtime=client is not None):
        return

    today     = datetime.now(ET).strftime('%Y-%m-%d')
    daily_pnl = get_daily_pnl(FUTURES_DB_PATH, today)
    if is_daily_loss_limit_hit(daily_pnl, RISK_RULES['daily_loss_limit']):
        log.warning('Daily loss limit hit ($%.2f) — skipping scan', daily_pnl)
        global _daily_loss_notified_date
        if _daily_loss_notified_date != today:
            notifier.notify_system(f'Daily loss limit hit (${daily_pnl:.2f}) — trading paused for the day', level='critical')
            _daily_loss_notified_date = today
        return

    # Daily profit target — lock in the day's gain, don't give it back
    profit_target = RISK_RULES.get('daily_profit_target', 0)
    if profit_target and daily_pnl >= profit_target:
        log.info('Daily profit target hit ($%.2f >= $%.2f) — banking the day', daily_pnl, profit_target)
        global _daily_profit_notified_date
        if _daily_profit_notified_date != today:
            notifier.notify_system(f'Daily profit target hit (+${daily_pnl:.2f}) — trading paused, day banked', level='info')
            _daily_profit_notified_date = today
        return

    if get_setting(FUTURES_DB_PATH, 'trading_paused', 'false') == 'true':
        log.debug('Trading paused — skipping entries')
        return

    sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'

    today_date  = datetime.now(ET).strftime('%Y-%m-%d')
    now_iso     = datetime.now(ET).isoformat()
    event_times = get_today_event_times(FUTURES_DB_PATH, today_date)
    from bot.futures.risk import news_regime
    news_state = news_regime(now_iso, event_times,
                             blackout_min=RISK_RULES['news_blackout_minutes'],
                             near_min=15)
    if news_state['state'] == 'pause':
        log.info('News blackout active (event %.1f min away) — skipping scan', news_state['minutes_to'] or 0)
        return

    try:
        prices = get_yf_prices(SYMBOLS, tradovate_client=client)
    except Exception:
        log.exception('Failed to fetch prices')
        return

    if not prices:
        log.warning('No prices returned — skipping scan, preserving last market status')
        return

    orb_period    = _is_orb_period()
    orb_start_min = _orb_start_minute()
    orb_end_min   = _orb_end_minute()
    now_min       = _now_minute()
    now_iso       = datetime.now(ET).isoformat()

    status_map = {}

    et_hour_now = datetime.now(ET).hour

    for symbol in SYMBOLS:
        price = prices.get(symbol)
        if price is None:
            continue

        # Time-of-day block — skip entries during historically losing hours per symbol.
        # Audit (60-day): ES @ 22:00 ET = 14% WR / -$925; ES @ 14-16 ET = afternoon chop.
        # State still updates below so indicators stay warm for when good hours resume.
        hour_blocked = et_hour_now in BLOCKED_HOURS_ET.get(symbol, set())

        tick = TICK_INFO[symbol]['tick']
        vwap_state    = _vwap_states.setdefault(symbol, VWAPState())
        orb_state     = _orb_states.setdefault(symbol, ORBState())
        channel_state = _channel_states.setdefault(symbol, ChannelState())
        sma_state     = _sma_states.setdefault(symbol, SMAState())
        rsi_state     = _rsi_states.setdefault(symbol, RSIState())
        vol_state     = _vol_states.setdefault(symbol, VolatilityState())

        channel_state.update(price)
        sma_state.update(price)
        rsi_state.update(price)
        vol_state.update(price)
        vol_state.update_baseline(price)
        vwap_state.add_bar(price=price, volume=1)
        _last_price[symbol] = price

        if not orb_state._ready and now_min >= orb_end_min:
            orb_state.set_ready()

        if orb_period:
            orb_state.update(price=price, ts_minute=now_min)
            orb_hi = orb_state.high if orb_state.high != float('-inf') else None
            orb_lo = orb_state.low  if orb_state.low  != float('inf')  else None
            status_map[symbol] = {'price': price, 'session': 'orb', 'orb_high': orb_hi, 'orb_low': orb_lo}
            continue

        rsi     = rsi_state.value()
        vwap    = calc_vwap(vwap_state)
        sma     = sma_state.value()
        signal  = None
        strategy = None
        blocked_by = None

        vwap_pct = SYMBOL_VWAP_PCT.get(symbol, STRATEGY_PARAMS['vwap_deviation_pct'])
        dev_pct  = round((price - vwap) / vwap * 100, 4) if vwap else None
        trend    = ('up' if price > sma else 'down') if sma else None

        # Adaptive threshold: use rolling volatility when available, else config default
        dynamic_threshold = vol_state.threshold() or vwap_pct

        # Day-type classification — computed once per session after 10:00 ET, then cached.
        # Cleared on 6 PM ET daily reset.
        if et_hour_now >= 10 and symbol not in _day_type_cache:
            tick = TICK_INFO[symbol]['tick']
            prev_close_str = get_setting(FUTURES_DB_PATH, f'prev_close_{symbol}', '')
            prev_close = float(prev_close_str) if prev_close_str else None
            day_type = classify_day_type(
                prev_close=prev_close,
                orb_state=orb_state,
                vwap=vwap,
                current_price=price,
                sma=sma,
                atr=vol_state.atr(),
                atr_baseline=vol_state.atr_baseline(),
                tick=tick,
            )
            _day_type_cache[symbol] = day_type
            set_setting(FUTURES_DB_PATH, f'day_type_{symbol}', day_type)
            log.info('%s day type: %s  (gap_close=%s orb=%.2f-%.2f atr=%.2f baseline=%s)',
                     symbol, day_type, prev_close, orb_state.low, orb_state.high,
                     vol_state.atr() or 0,
                     f'{vol_state.atr_baseline():.2f}' if vol_state.atr_baseline() else 'n/a')
            try:
                notifier.notify_system(f'{symbol} day type: {day_type}', level='info')
            except Exception:
                pass

        # Read tuned thresholds — fall back to dynamic/config until tuner has enough data
        sym_l = symbol.lower()
        tuned_rsi_long  = float(get_setting(FUTURES_DB_PATH, f'tune_{sym_l}_long_rsi',  40))
        tuned_rsi_short = float(get_setting(FUTURES_DB_PATH, f'tune_{sym_l}_short_rsi', 60))
        tuned_dev_long  = float(get_setting(FUTURES_DB_PATH, f'tune_{sym_l}_long_dev',  dynamic_threshold))
        tuned_dev_short = float(get_setting(FUTURES_DB_PATH, f'tune_{sym_l}_short_dev', dynamic_threshold))

        if vwap is not None and dev_pct is not None:
            # Bounce-confirmed reversion: only enter after price retraces 25% from peak deviation
            direction = _check_reversion_entry(symbol, dev_pct, tuned_dev_long, tuned_dev_short)

            pending = _peak_dev.get(symbol)

            # High-confidence override on bounce: if armed AND confidence for the pending
            # direction is >= override threshold, skip the retrace wait and fire now.
            # A 92 score shouldn't sit waiting for a 0.05% retrace that may never come.
            if direction is None and pending:
                atr_now      = vol_state.atr()
                atr_baseline = vol_state.atr_baseline()
                atr_ratio    = (atr_now / atr_baseline) if (atr_now and atr_baseline) else None
                _bias = get_market_bias(FUTURES_DB_PATH, symbol)
                pending_dir = pending['side']
                pending_conf, _ = compute_confidence(
                    direction=pending_dir, dev_pct=dev_pct,
                    dev_threshold=tuned_dev_long if pending_dir == 'long' else tuned_dev_short,
                    sma_trend=trend, day_type=_day_type_cache.get(symbol),
                    rsi=rsi, atr_ratio=atr_ratio,
                    near_event=news_state['state'] == 'near_event',
                    bias_disagrees=bool(_bias and _bias != pending_dir),
                )
                override_threshold = RISK_RULES.get('confidence_override', 70)
                if pending_conf >= override_threshold:
                    log.info('%s %s — confidence %d >= %d, BYPASSING bounce confirmation',
                             symbol, pending_dir, pending_conf, override_threshold)
                    direction = pending_dir
                    _peak_dev.pop(symbol, None)
                    pending = None
                else:
                    blocked_by = f'pending {pending_dir} (peak {pending["peak"]:.3f}%, conf {pending_conf}<{override_threshold})'

            # Compute confidence FIRST so high-conviction setups can bypass
            # the single-indicator filters below.
            preview_confidence = None
            if direction and dev_pct is not None:
                atr_now      = vol_state.atr()
                atr_baseline = vol_state.atr_baseline()
                atr_ratio    = (atr_now / atr_baseline) if (atr_now and atr_baseline) else None
                _bias = get_market_bias(FUTURES_DB_PATH, symbol)
                preview_confidence, _ = compute_confidence(
                    direction=direction,
                    dev_pct=dev_pct,
                    dev_threshold=tuned_dev_long if direction == 'long' else tuned_dev_short,
                    sma_trend=trend,
                    day_type=_day_type_cache.get(symbol),
                    rsi=rsi,
                    atr_ratio=atr_ratio,
                    near_event=news_state['state'] == 'near_event',
                    bias_disagrees=bool(_bias and _bias != direction),
                )

            override_threshold = RISK_RULES.get('confidence_override', 70)
            confidence_override = (preview_confidence is not None
                                    and preview_confidence >= override_threshold)

            if confidence_override:
                log.info('%s %s — confidence %d >= %d, BYPASSING indicator filters',
                         symbol, direction, preview_confidence, override_threshold)
            else:
                # Day-type filter — block reversion entries on trend/gap days
                dt_block = day_type_blocks_direction(_day_type_cache.get(symbol), direction or '')
                if direction and dt_block:
                    log.info('%s %s blocked by day type: %s', symbol, direction, dt_block)
                    blocked_by = dt_block
                    direction = None

                # Micro-momentum filter — catches the case where 20-bar SMA still says
                # "up" but price has been falling for the last few bars.
                if direction:
                    mm_block = micro_momentum_blocks(channel_state, direction)
                    if mm_block:
                        log.info('%s %s blocked: %s', symbol, direction, mm_block)
                        blocked_by = mm_block
                        direction = None

                # Hard trend filter — NO counter-trend reversions in normal regime.
                if direction == 'long' and trend == 'down':
                    direction = None
                    blocked_by = 'trend=down (no longs)'
                elif direction == 'short' and trend == 'up':
                    direction = None
                    blocked_by = 'trend=up (no shorts)'

            if direction:
                # RSI is now an INPUT to the confidence score, not a hard gate.
                signal, strategy = direction, 'vwap'

        orb_dir = check_orb_signal(price, orb_state, orb_end_min,
                                    STRATEGY_PARAMS['orb_min_range_ticks'], tick)
        if orb_dir:
            signal, strategy = orb_dir, 'orb'
            blocked_by = None

        # News bias is now a SOFT volatility input — not a hard block.
        # Trades against bias get half size (handled in trader.py via signal['bias_disagrees']).
        # Trades with bias keep normal size.
        news_bias_for_symbol = None
        if signal:
            news_bias_for_symbol = get_market_bias(FUTURES_DB_PATH, symbol)

        # Confidence score 0-100 — compute for BOTH directions so the dashboard
        # can always show long AND short scores side by side, regardless of which
        # direction the bot is currently considering.
        atr_now      = vol_state.atr()
        atr_baseline = vol_state.atr_baseline()
        atr_ratio    = (atr_now / atr_baseline) if (atr_now and atr_baseline) else None
        bias = news_bias_for_symbol

        confidence_long, breakdown_long = compute_confidence(
            direction='long',
            dev_pct=dev_pct, dev_threshold=tuned_dev_long,
            sma_trend=trend, day_type=_day_type_cache.get(symbol),
            rsi=rsi, atr_ratio=atr_ratio,
            near_event=news_state['state'] == 'near_event',
            bias_disagrees=bool(bias and bias != 'long'),
        ) if dev_pct is not None else (None, None)
        confidence_short, breakdown_short = compute_confidence(
            direction='short',
            dev_pct=dev_pct, dev_threshold=tuned_dev_short,
            sma_trend=trend, day_type=_day_type_cache.get(symbol),
            rsi=rsi, atr_ratio=atr_ratio,
            near_event=news_state['state'] == 'near_event',
            bias_disagrees=bool(bias and bias != 'short'),
        ) if dev_pct is not None else (None, None)

        # "Active" direction (for the hard gate + dashboard highlight)
        scored_direction = signal
        if not scored_direction:
            pending = _peak_dev.get(symbol)
            if pending:
                scored_direction = pending.get('side')
            elif dev_pct is not None:
                if dev_pct <= -tuned_dev_long:
                    scored_direction = 'long'
                elif dev_pct >= tuned_dev_short:
                    scored_direction = 'short'
        confidence_score = (confidence_long if scored_direction == 'long'
                            else confidence_short if scored_direction == 'short'
                            else None)
        confidence_breakdown = (breakdown_long if scored_direction == 'long'
                                else breakdown_short if scored_direction == 'short'
                                else None)

        # Hard gate: only block actual firing signals if below threshold
        if signal:
            min_conf = RISK_RULES.get('min_confidence', 60)
            if confidence_score is not None and confidence_score < min_conf:
                log.info('%s %s blocked by confidence %d < %d (%s)', symbol, signal,
                         confidence_score, min_conf, confidence_breakdown)
                blocked_by = f'confidence {confidence_score}/100 < {min_conf}'
                signal, strategy = None, None

        # Time-of-day block — applied last so the signal/blocked_by reflects the real reason
        if signal and hour_blocked:
            log.info('%s %s blocked — hour %02d:00 ET historically loses', symbol, signal, et_hour_now)
            blocked_by = f'hour {et_hour_now:02d}:00 ET (bad-hour block)'
            signal, strategy = None, None

        orb_hi = orb_state.high if orb_state._ready and orb_state.high != float('-inf') else None
        orb_lo = orb_state.low  if orb_state._ready and orb_state.low  != float('inf')  else None
        status_map[symbol] = {
            'price':      price,
            'vwap':       round(vwap, 2) if vwap else None,
            'dev_pct':    dev_pct,
            'threshold':  round((tuned_dev_long + tuned_dev_short) / 2, 4),
            'sma':        round(sma, 2) if sma else None,
            'rsi':        round(rsi, 1) if rsi else None,
            'trend':      trend,
            'day_type':   _day_type_cache.get(symbol),
            'signal':     signal,
            'blocked_by': blocked_by,
            'confidence':       confidence_score,           # active direction (for highlight)
            'conf_break':       confidence_breakdown,
            'conf_dir':         scored_direction,
            'confidence_long':  confidence_long,            # always-on side-by-side scores
            'confidence_short': confidence_short,
            'conf_break_long':  breakdown_long,
            'conf_break_short': breakdown_short,
            'news_state':       news_state['state'],
            'session':          'active',
            'orb_high':         round(orb_hi, 2) if orb_hi else None,
            'orb_low':          round(orb_lo, 2) if orb_lo else None,
        }

        # Channel disabled — stale data causes it to buy tops and sell bottoms

        if signal is None:
            continue

        signal_id = insert_signal(FUTURES_DB_PATH, {
            'ts': now_iso, 'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'vwap': vwap,
            'orb_high': orb_state.high if orb_state._ready else None,
            'orb_low':  orb_state.low  if orb_state._ready else None,
            'traded': 0,
        })
        contracts = 2 if dev_pct is not None and abs(dev_pct) >= 2 * dynamic_threshold else 1
        # Day-type sizing adjustment: news_expansion halves, low_vol_chop floors to 1
        _dt = _day_type_cache.get(symbol)
        if _dt == 'news_expansion':
            contracts = max(1, contracts // 2)
            log.info('news_expansion regime — halving size to %d for %s', contracts, symbol)
        elif _dt == 'low_vol_chop':
            contracts = 1
            log.info('low_vol_chop regime — flooring size to 1 for %s', contracts)
        # News regime sizing: near a scheduled event = half size + wider stop + tighter target
        bias_disagrees = bool(news_bias_for_symbol and news_bias_for_symbol != signal)
        if news_state['state'] == 'near_event':
            contracts = max(1, contracts // 2)
            log.info('near_event regime (%.1f min) — halving size to %d for %s',
                     news_state['minutes_to'] or 0, contracts, symbol)
        if bias_disagrees:
            contracts = max(1, contracts // 2)
            log.info('news bias (%s) disagrees with %s signal — halving size to %d for %s',
                     news_bias_for_symbol, signal, contracts, symbol)
        log.info('Signal: %s %s %s @ %.2f contracts=%d', strategy, signal, symbol, price, contracts)
        place_entry(client, FUTURES_DB_PATH, {
            'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'signal_id': signal_id,
            'entry_rsi': rsi, 'entry_dev_pct': dev_pct, 'vwap': vwap,
            'trend': trend, 'atr': vol_state.atr(),
            # News volatility engine inputs
            'near_event': news_state['state'] == 'near_event',
            'bias_disagrees': bias_disagrees,
        }, contracts=contracts, sim=sim)

    # Persist status so the dashboard can show live conditions
    import json as _json
    try:
        status_map['_session'] = 'orb' if orb_period else 'active'
        status_map['_ts'] = now_iso
        set_setting(FUTURES_DB_PATH, 'market_status', _json.dumps(status_map))
    except Exception:
        pass


def job_manage(client):
    if not _is_market_hours(has_realtime=client is not None):
        return
    try:
        prices = get_yf_prices(SYMBOLS, tradovate_client=client)
        sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
        manage_futures_positions(client, FUTURES_DB_PATH, current_prices=prices, sim=sim)
    except Exception:
        log.exception('Manager error')


def job_snapshot(client):
    try:
        if client is not None:
            bal = client.get_account_balance()
            net_liq = float(bal.get('netLiquidatingValue', 0))
            cash    = float(bal.get('cashBalance', 0))
            open_pnl = float(bal.get('openTradeEquity', 0))
            realized = float(bal.get('realizedPnL', 0))
        else:
            # Sim mode — derive from DB
            from bot.futures.db import get_all_time_pnl
            today = datetime.now(ET).strftime('%Y-%m-%d')
            realized = get_daily_pnl(FUTURES_DB_PATH, today)
            net_liq  = 500.0 + get_all_time_pnl(FUTURES_DB_PATH)
            cash     = net_liq
            open_pnl = 0.0
        insert_snapshot(FUTURES_DB_PATH, {
            'ts':                 datetime.now(ET).isoformat(),
            'net_liq':            net_liq,
            'cash':               cash,
            'open_pnl':           open_pnl,
            'realized_pnl_today': realized,
        })
    except Exception:
        log.exception('Snapshot error')


def job_news():
    try:
        fh_key = get_setting(FUTURES_DB_PATH, 'finnhub_api_key', '')
        fetch_and_store_news(FUTURES_DB_PATH, AV_API_KEY, finnhub_api_key=fh_key)
    except Exception:
        log.exception('News fetch error')


def job_eod_flat(client):
    """Force-close every open position before TopStep's EOD flat cutoff.

    TopStep funded/eval accounts require all positions flat by 4:10 PM ET
    (3:10 PM CT). We run this job a few minutes before that, close every open
    trade at the current price with close_reason='eod_flat'.
    """
    from bot.futures.db import get_open_trades
    from bot.futures.trader import close_trade
    try:
        opens = get_open_trades(FUTURES_DB_PATH)
        if not opens:
            log.info('EOD flat: no open positions, nothing to do')
            return
        notifier.notify_system(f'EOD flat triggered — closing {len(opens)} open position(s) before TopStep cutoff', level='warning')
        prices = get_yf_prices(SYMBOLS, tradovate_client=client) if client else {}
        sim    = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
        for trade in opens:
            symbol = trade['symbol']
            current = prices.get(symbol) or float(trade.get('current_price') or trade['entry_price'])
            log.warning('EOD flat: closing %s %s id=%s @ %.2f', symbol, trade['direction'], trade['id'], current)
            try:
                close_trade(client, FUTURES_DB_PATH, trade, current, 'eod_flat', sim=sim)
            except Exception:
                log.exception('EOD flat: failed to close trade id=%s', trade['id'])
    except Exception:
        log.exception('EOD flat job error')


def main():
    init_db(FUTURES_DB_PATH)
    _reset_daily_state()
    _warmup_state()

    # Telegram notifier — pulls token/chat_id from futures_settings (set via Settings page)
    tg_token   = get_setting(FUTURES_DB_PATH, 'telegram_token',   '')
    tg_chat_id = get_setting(FUTURES_DB_PATH, 'telegram_chat_id', '')
    notifier.init(tg_token, tg_chat_id)

    tv_username  = get_setting(FUTURES_DB_PATH, 'tv_username',  '')
    tv_password  = get_setting(FUTURES_DB_PATH, 'tv_password',  '')
    tv_cid       = get_setting(FUTURES_DB_PATH, 'tv_cid',       '')
    tv_sec       = get_setting(FUTURES_DB_PATH, 'tv_sec',       '')
    tv_device_id = get_setting(FUTURES_DB_PATH, 'tv_device_id', 'sharp-bot-futures-001')
    tv_demo      = get_setting(FUTURES_DB_PATH, 'tv_demo',      'true').lower() == 'true'

    sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
    client = None

    # Broker selection priority:
    #   1. TopstepX (when configured) — full replacement: prices + orders + account
    #   2. Tastytrade DXFeed (fallback) — prices only, no order routing
    #   3. None — sim mode, prices via Finnhub/Yahoo Finance
    import os
    ts_user    = get_setting(FUTURES_DB_PATH, 'topstep_username',   '') or os.environ.get('TOPSTEP_USERNAME', '')
    ts_key     = get_setting(FUTURES_DB_PATH, 'topstep_api_key',    '') or os.environ.get('TOPSTEP_API_KEY',  '')
    ts_account = get_setting(FUTURES_DB_PATH, 'topstep_account_id', '') or os.environ.get('TOPSTEP_ACCOUNT',  '')
    broker_label = 'sim'
    if ts_user and ts_key and ts_account:
        try:
            from bot.futures.topstep_client import TopstepXClient
            ts = TopstepXClient(ts_user, ts_key, ts_account)
            if ts.connect():
                client = ts
                broker_label = f'TopstepX (account {ts_account})'
                log.info('TopstepX connected — prices + orders + account routing through TopStep')
                notifier.notify_system(f'TopstepX connected — account {ts_account} live', level='info')
            else:
                err = getattr(ts, '_last_error', 'unknown') or 'unknown'
                log.warning('TopstepX credentials set but connect() returned False — falling back. Reason: %s', err)
                notifier.notify_system(f'TopstepX connect FAILED ({err}) — falling back to Tastytrade/sim', level='warning')
        except Exception as e:
            log.exception('TopstepX connection failed — falling back to Tastytrade')
            notifier.notify_system(f'TopstepX connection error: {e} — falling back', level='error')

    if client is None:
        tt_provider = get_setting(FUTURES_DB_PATH, 'tt_provider_secret', '') or os.environ.get('TT_SECRET', '')
        tt_refresh  = get_setting(FUTURES_DB_PATH, 'tt_refresh_token',   '') or os.environ.get('TT_REFRESH', '')
        tt_account  = get_setting(FUTURES_DB_PATH, 'tt_account_number',  '') or os.environ.get('TT_ACCOUNT', '')
        if tt_provider and tt_refresh and tt_account:
            try:
                tt = _TastyClient(tt_provider, tt_refresh, tt_account)
                tt.connect()
                client = tt
                broker_label = 'Tastytrade DXFeed (prices only, sim orders)'
                log.info('Tastytrade DXFeed connected — real-time futures prices off-hours')
            except Exception:
                log.exception('Tastytrade connection failed — falling back to Finnhub/Yahoo Finance')
        else:
            log.info('No broker credentials — sim mode, prices via Finnhub/Yahoo Finance')

    def _scan():     job_scan(client)
    def _manage():   job_manage(client)
    def _snapshot(): job_snapshot(client)
    def _reset():    _reset_daily_state()

    scheduler = BlockingScheduler(timezone=ET)
    scheduler.add_job(_scan,     IntervalTrigger(seconds=10, timezone=ET), id='scan')
    scheduler.add_job(_manage,   IntervalTrigger(seconds=5, timezone=ET), id='manage')
    # Snapshot every hour during active session (6 PM – 5 PM next day, skip 5–6 PM window)
    scheduler.add_job(_snapshot, CronTrigger(hour='0-16,18-23', minute=0, timezone=ET), id='snapshot')
    # Reset VWAP/ORB at 6 PM ET — start of new futures session
    scheduler.add_job(_reset,    CronTrigger(hour=18, minute=0, timezone=ET), id='reset')
    # EOD flat — close every open position before TopStep's 4:10 PM ET cutoff
    scheduler.add_job(
        lambda: job_eod_flat(client),
        CronTrigger(hour=TOPSTEP_RULES['eod_flat_hour_et'], minute=TOPSTEP_RULES['eod_flat_minute'], timezone=ET),
        id='eod_flat',
    )
    # Record session-close price for next session's day-type classifier (gap detection)
    scheduler.add_job(_record_session_close, CronTrigger(hour=16, minute=58, timezone=ET), id='session_close')
    # Auto-tuner DISABLED — it was learning from a mix of old/new strategy trades
    # and writing bad thresholds (tune_nq_long_dev=0.14 contributed to 7 consecutive
    # NQ long stop-outs). Re-enable only after we have 30+ clean trades per direction
    # under the new strategy.
    # scheduler.add_job(lambda: run_tuner(FUTURES_DB_PATH), CronTrigger(hour='9-16', minute=0, second=0, timezone=ET), id='tuner')

    # News every 2 min — uses Finnhub when key is set, falls back to yfinance
    def job_news_frequent():
        try:
            fh_key = get_setting(FUTURES_DB_PATH, 'finnhub_api_key', '')
            yf_only = not bool(fh_key)  # use full fetch when Finnhub key is set
            fetch_and_store_news(FUTURES_DB_PATH, AV_API_KEY, finnhub_api_key=fh_key, yf_only=yf_only)
        except Exception: log.exception('news fetch error')
    scheduler.add_job(job_news_frequent, IntervalTrigger(seconds=30, timezone=ET), id='news_frequent')
    # Full fetch (Nasdaq calendar + AV) once per hour
    scheduler.add_job(job_news, CronTrigger(hour='0-16,18-23', minute=0, timezone=ET), id='news_full')
    scheduler.add_job(job_news, 'date', run_date=datetime.now(ET), id='news_startup')

    log.info('Futures bot running [%s] — broker: %s. Ctrl+C to stop.',
             'SIM' if sim else ('DEMO' if tv_demo else 'LIVE'), broker_label)
    notifier.notify_system(f'Bot started — mode: {"SIM" if sim else ("DEMO" if tv_demo else "LIVE")}, broker: {broker_label}')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Shutting down.')
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
