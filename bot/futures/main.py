# bot/futures/main.py
import logging
from datetime import datetime, timezone, timedelta
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from bot.futures.config import (
    FUTURES_DB_PATH, SYMBOLS, TICK_INFO, STRATEGY_PARAMS, RISK_RULES,
    TIMEZONE, MARKET_CLOSE_HOUR, MARKET_OPEN_HOUR, ORB_START, ORB_END,
    AV_API_KEY, SYMBOL_VWAP_PCT, SYMBOL_NEWS_KEYWORDS, BLOCKED_HOURS_ET,
    TOPSTEP_RULES, SYMBOL_MAX_CONTRACTS,
    ENABLE_TREND_STRATEGY, SHORT_DEV_MULTIPLIER, STRONG_TREND_DAY_TYPES,
    USE_REAL_VWAP, ENABLE_EXHAUSTION_FADE, EXH_FADE_LOOKBACK_BARS,
    EXH_FADE_VOL_MULT, EXH_FADE_MIN_DEV_PCT,
)
from bot.futures.db import (
    init_db, insert_signal, get_daily_pnl, get_daily_pnl_range, get_setting, set_setting,
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
    micro_momentum_blocks, check_trend_pullback, check_exhaustion_fade,
)
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
_consec_below_vwap: dict = {}  # symbol -> count of consecutive scans price < VWAP (regime gate)
_consec_above_vwap: dict = {}  # symbol -> count of consecutive scans price > VWAP
_daily_profit_notified_date: str = ''  # 'YYYY-MM-DD' — prevents duplicate profit-target Telegram pings
_vwap_last_update: dict = {}     # symbol -> epoch of last real-VWAP rebuild (throttle to ~60s)
_vwap_bars_cache:  dict = {}     # symbol -> last fetched 1-min bars (reused by exhaustion fade)
_ref_levels_cache: dict = {}     # symbol -> session reference levels (prior-day/overnight), cached per session
_dd_last_check_ts: float = 0.0   # epoch — throttles the live TopStep balance/canTrade poll
_dd_halted:        bool  = False # cached: account locked or near the trailing-DD floor (halt this session)
_dd_notified_date: str   = ''    # 'YYYY-MM-DD' — prevents duplicate drawdown-halt pings


def _check_reversion_entry(symbol, dev_pct, thresh_long, thresh_short, retrace=0.25, retrace_cap_pct=0.10):
    """VWAP reversion with bounce confirmation.

    Tracks peak deviation while price is extended. Returns a direction only after
    price retraces back toward VWAP from the peak — by `retrace` fraction OR
    `retrace_cap_pct` absolute deviation, whichever is SMALLER.

    The absolute cap matters on big extensions: a 25%-of-peak retrace on a 0.88%
    stretch needs a ~64pt NQ reversal just to confirm, so real ~30pt turns were
    missed (2026-05-20). The cap lets it confirm after a reasonable pullback no
    matter how far price stretched. For moderate deviations (peak < ~0.40%) the
    percentage rule still binds first, so normal behavior is unchanged.
    """
    state = _peak_dev.get(symbol)

    # Stale state — price crossed VWAP, abandon pending entry
    if state and ((state['side'] == 'long'  and dev_pct >= 0) or
                  (state['side'] == 'short' and dev_pct <= 0)):
        _peak_dev.pop(symbol, None)
        state = None

    def _confirmed(side, peak):
        trigger  = min(retrace * abs(peak), retrace_cap_pct)
        retraced = (dev_pct - peak) if side == 'long' else (peak - dev_pct)
        return retraced >= trigger

    if dev_pct <= -thresh_long:
        if state is None or state['side'] != 'long':
            _peak_dev[symbol] = {'side': 'long', 'peak': dev_pct}
            return None
        if dev_pct < state['peak']:
            state['peak'] = dev_pct  # still extending — update peak, wait
            return None
        if _confirmed('long', state['peak']):
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
        if _confirmed('short', state['peak']):
            _peak_dev.pop(symbol, None)
            return 'short'
        return None

    # Inside threshold band but state pending — fire if bounce already crossed target
    if state and _confirmed(state['side'], state['peak']):
        d = state['side']
        _peak_dev.pop(symbol, None)
        return d

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
    global _vwap_states, _orb_states, _channel_states, _sma_states, _rsi_states, _vol_states, _peak_dev, _day_type_cache, _vwap_last_update, _vwap_bars_cache, _dd_halted, _dd_notified_date, _ref_levels_cache
    _vwap_last_update = {}   # force a fresh real-VWAP rebuild from the new session's bars
    _vwap_bars_cache  = {}
    _dd_halted = False       # re-evaluate the drawdown halt next session (peak persists)
    _dd_notified_date = ''
    _ref_levels_cache = {}   # recompute prior-day/overnight levels for the new session
    _vwap_states    = {s: VWAPState()        for s in SYMBOLS}
    _orb_states     = {s: ORBState()         for s in SYMBOLS}
    _channel_states = {s: ChannelState()     for s in SYMBOLS}
    _sma_states     = {s: SMAState()         for s in SYMBOLS}
    _rsi_states     = {s: RSIState()         for s in SYMBOLS}
    _vol_states     = {s: VolatilityState()  for s in SYMBOLS}
    _peak_dev       = {}
    _day_type_cache = {}
    log.info('Daily state reset — VWAP, ORB, channel, SMA, RSI, volatility, day-type, and pending entries cleared')


def _session_open_utc():
    """Start of the current futures session (18:00 ET) as a UTC datetime — the
    anchor for session VWAP. Matches the 18:00 ET daily reset."""
    from datetime import timedelta as _td
    now_et = datetime.now(ET)
    anchor = now_et.replace(hour=18, minute=0, second=0, microsecond=0)
    if now_et < anchor:
        anchor -= _td(days=1)
    return anchor.astimezone(timezone.utc)


def _rebuild_real_vwap(client, symbol, vwap_state):
    """Rebuild the session VWAP from real 1-min bar volume since the session open.

    Rebuilds from scratch each call (reset + feed all completed bars) so the VWAP
    is always the true session figure with no drift or double-counting. Returns
    the fetched bar list (truthy) on success, or None if no usable bars (caller
    keeps the unweighted fallback). The bars are also reused by the exhaustion fade.
    """
    now = datetime.now(timezone.utc)
    start = _session_open_utc()
    bars = client.get_bars(symbol,
                           start.isoformat().replace('+00:00', 'Z'),
                           now.isoformat().replace('+00:00', 'Z'))
    if not bars:
        return None
    vwap_state.reset()
    fed = 0
    for b in bars:
        try:
            vol = float(b.get('v', 0) or 0)
            if vol <= 0:
                continue
            typical = (float(b['h']) + float(b['l']) + float(b['c'])) / 3.0
            vwap_state.add_bar(price=typical, volume=vol)
            fed += 1
        except (KeyError, TypeError, ValueError):
            continue
    return bars if fed > 0 else None


def _topstep_drawdown_halt(client, today: str) -> bool:
    """Live trailing-drawdown guard. Halts NEW entries before the account breaches
    its trailing max-loss floor (or if it's already locked, or the Combine profit
    target is reached). Polls the live balance ~every 30s and tracks a persistent
    high-water mark; the halt decision is cached for the session. Returns True to halt.
    """
    global _dd_last_check_ts, _dd_halted, _dd_notified_date
    if _dd_halted:
        return True
    import time as _t
    # Instant breach via the user stream (in-memory, every scan) if available.
    if hasattr(client, 'get_live_account'):
        la = client.get_live_account()
        if la and la.get('canTrade') is False:
            _dd_halted = True
            _notify_halt(today, f"account locked (canTrade=False, balance=${la.get('balance', 0):.0f})")
            return True
    # Throttle the REST balance poll to ~30s.
    if (_t.time() - _dd_last_check_ts) < 30:
        return _dd_halted
    _dd_last_check_ts = _t.time()
    try:
        bal = client.get_account_balance()
    except Exception:
        log.exception('Drawdown guard balance fetch failed — not halting on transient error')
        return _dd_halted
    net_liq   = float(bal.get('netLiquidatingValue', 0))
    can_trade = bal.get('canTrade', True)

    start    = TOPSTEP_RULES.get('start_balance', 50000.0)
    trailing = TOPSTEP_RULES.get('trailing_drawdown', 2000.0)
    buffer   = TOPSTEP_RULES.get('trailing_halt_buffer', 400.0)
    target   = TOPSTEP_RULES.get('profit_target_total', 0.0)

    # Persistent high-water mark (trailing floor follows the peak).
    peak = float(get_setting(FUTURES_DB_PATH, 'topstep_peak_balance', 0) or 0)
    if peak <= 0:
        peak = max(net_liq, start)
    if net_liq > peak:
        peak = net_liq
    set_setting(FUTURES_DB_PATH, 'topstep_peak_balance', str(peak))

    from bot.futures.risk import drawdown_halt_decision
    halt, reason = drawdown_halt_decision(net_liq, peak, can_trade, start, trailing, buffer, target)
    if halt:
        _dd_halted = True
        _notify_halt(today, reason)
    return halt


def _notify_halt(today: str, reason: str):
    global _dd_notified_date
    log.warning('TRADING HALTED — %s', reason)
    if _dd_notified_date != today:
        try:
            notifier.notify_system(f'Trading HALTED — {reason}', level='critical')
        except Exception:
            pass
        _dd_notified_date = today


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
    # Compute the UTC bounds of the current ET trading day so evening trades
    # (which roll into the next UTC date) are counted correctly.
    from datetime import timedelta as _td
    _et_now      = datetime.now(ET)
    _et_midnight = ET.localize(datetime(_et_now.year, _et_now.month, _et_now.day))
    _start_utc   = _et_midnight.astimezone(timezone.utc).isoformat()
    _end_utc     = (_et_midnight + _td(days=1)).astimezone(timezone.utc).isoformat()
    daily_pnl    = get_daily_pnl_range(FUTURES_DB_PATH, _start_utc, _end_utc)

    # Daily profit target — lock in the day's gain, don't give it back
    profit_target = RISK_RULES.get('daily_profit_target', 0)
    if profit_target and daily_pnl >= profit_target:
        log.info('Daily profit target hit ($%.2f >= $%.2f) — banking the day', daily_pnl, profit_target)
        global _daily_profit_notified_date
        if _daily_profit_notified_date != today:
            notifier.notify_system(f'Daily profit target hit (+${daily_pnl:.2f}) — trading paused, day banked', level='info')
            _daily_profit_notified_date = today
        return

    # NOTE: trading_paused is NOT checked here. It's checked at entry time so the
    # dashboard still gets fresh market_status updates while paused — only NEW
    # entries are blocked. See the place_entry call below.
    trading_paused = get_setting(FUTURES_DB_PATH, 'trading_paused', 'false') == 'true'

    sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'

    today_date  = datetime.now(ET).strftime('%Y-%m-%d')
    now_iso     = datetime.now(ET).isoformat()

    # Live trailing-drawdown guard — block NEW entries before the account breaches
    # its trailing max-loss floor (checked at entry time so the dashboard keeps
    # updating). Sim is exempt. Throttled balance poll lives inside the guard.
    dd_halted = False
    if not sim and client is not None and hasattr(client, 'get_account_balance'):
        try:
            dd_halted = _topstep_drawdown_halt(client, today_date)
        except Exception:
            log.exception('Drawdown guard error — not halting')
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
        # VWAP: real volume-weighted (rebuilt from 1-min bars, ~60s cadence) when a
        # bar source is available; otherwise the unweighted per-scan average.
        # Once real VWAP is active for a symbol we never feed volume=1 again this
        # session (a failed rebuild keeps the last good VWAP rather than corrupting it).
        if USE_REAL_VWAP and client is not None and hasattr(client, 'get_bars'):
            import time as _t
            if _t.time() - _vwap_last_update.get(symbol, 0) >= 55:
                try:
                    _bars = _rebuild_real_vwap(client, symbol, vwap_state)
                    if _bars:
                        _vwap_last_update[symbol] = _t.time()
                        _vwap_bars_cache[symbol] = _bars
                except Exception:
                    log.exception('Real-VWAP rebuild failed for %s — keeping prior VWAP', symbol)
            if symbol not in _vwap_last_update:   # never built yet — bridge with unweighted
                vwap_state.add_bar(price=price, volume=1)
        else:
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

        # Persistent VWAP regime — counts consecutive scans price has been on each side.
        # Hard block downstream: if price has been below VWAP for N+ scans, no longs;
        # if above for N+ scans, no shorts. Faster + more reliable than SMA trend filter.
        if dev_pct is not None:
            if dev_pct < 0:
                _consec_below_vwap[symbol] = _consec_below_vwap.get(symbol, 0) + 1
                _consec_above_vwap[symbol] = 0
            elif dev_pct > 0:
                _consec_above_vwap[symbol] = _consec_above_vwap.get(symbol, 0) + 1
                _consec_below_vwap[symbol] = 0

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
        # Tighten shorts — require more deviation than longs (weaker short edge).
        tuned_dev_short *= SHORT_DEV_MULTIPLIER

        if vwap is not None and dev_pct is not None:
            # Bounce-confirmed reversion: only enter after price retraces 25% from peak deviation
            direction = _check_reversion_entry(symbol, dev_pct, tuned_dev_long, tuned_dev_short)

            pending = _peak_dev.get(symbol)
            if direction is None and pending:
                blocked_by = f'pending {pending["side"]} (peak {pending["peak"]:.3f}%)'

            # RSI gate (data-driven): shorts lose when RSI is high (fighting strong
            # up-momentum) — RSI 60-70 = -$69/trade, 70+ = -$36, while RSI <60 shorts
            # are net positive. Longs profit across the RSI range, so no long block.
            if direction == 'short' and rsi is not None and rsi >= RISK_RULES['reversion_rsi_short_max']:
                blocked_by = f"RSI {rsi:.0f} >= {RISK_RULES['reversion_rsi_short_max']} (up-momentum too strong to short)"
                log.info('%s short blocked — %s', symbol, blocked_by)
                direction = None
            elif direction == 'long' and rsi is not None and rsi >= RISK_RULES['reversion_rsi_long_max']:
                blocked_by = f"RSI {rsi:.0f} >= {RISK_RULES['reversion_rsi_long_max']} (overbought rip — long top risk)"
                log.info('%s long blocked — %s', symbol, blocked_by)
                direction = None

            # Day-type filter — block reversion entries on trend/gap days
            dt_block = day_type_blocks_direction(_day_type_cache.get(symbol), direction or '')
            if direction and dt_block:
                log.info('%s %s blocked by day type: %s', symbol, direction, dt_block)
                blocked_by = dt_block
                direction = None

            # Micro-momentum filter — block reversion against the last 3 bars
            if direction:
                mm_block = micro_momentum_blocks(channel_state, direction)
                if mm_block:
                    log.info('%s %s blocked: %s', symbol, direction, mm_block)
                    blocked_by = mm_block
                    direction = None

            # Hard trend filter — NO counter-trend reversions
            if direction == 'long' and trend == 'down':
                direction = None
                blocked_by = 'trend=down (no longs)'
            elif direction == 'short' and trend == 'up':
                direction = None
                blocked_by = 'trend=up (no shorts)'

            if direction:
                signal, strategy = direction, 'vwap'

        # Trend-following fallback — when reversion has no setup AND price is in a
        # sustained one-directional regime, trade WITH the trend on a pullback.
        # Short the bounces in a downtrend, buy the dips in an uptrend. This is what
        # lets the bot participate on pure trend days when reversion sits frozen.
        if signal is None and vwap is not None and ENABLE_TREND_STRATEGY:
            tf_dir = check_trend_pullback(
                channel_state,
                _consec_below_vwap.get(symbol, 0),
                _consec_above_vwap.get(symbol, 0),
            )
            if tf_dir:
                # Entry-quality gates for the trend-pullback strategy (the source of
                # the 2026-05-20 trend losses). Each rejects a bad-entry class seen
                # in the data; checked in order, first failure blocks.
                tf_block = None
                # 1) SMA trend must confirm. The VWAP-streak regime lags badly (VWAP is
                #    a session-cumulative average), so price can sit "above VWAP" while
                #    falling hard — the falling-knife longs (NQ 29168 -> 29090).
                if tf_dir == 'long' and trend == 'down':
                    tf_block = 'SMA trend down'
                elif tf_dir == 'short' and trend == 'up':
                    tf_block = 'SMA trend up'
                # 2) RSI momentum must be healthy. Losing "uptrend" longs had RSI
                #    19/32/34/36 — that's a collapse, not a pullback.
                elif tf_dir == 'long' and rsi is not None and rsi < RISK_RULES['trend_min_rsi_long']:
                    tf_block = f'RSI {rsi:.0f} < {RISK_RULES["trend_min_rsi_long"]} (weak long)'
                elif tf_dir == 'short' and rsi is not None and rsi > RISK_RULES['trend_max_rsi_short']:
                    tf_block = f'RSI {rsi:.0f} > {RISK_RULES["trend_max_rsi_short"]} (weak short)'
                # 3) Don't chase extension. The biggest losses came from high deviation
                #    from VWAP (dev 0.27-0.39%) = entering after an expansion candle.
                elif dev_pct is not None and abs(dev_pct) > RISK_RULES['trend_max_dev_pct']:
                    tf_block = f'dev {abs(dev_pct):.2f}% > {RISK_RULES["trend_max_dev_pct"]}% (overextended)'
                # 4) Price must be on the correct side of VWAP for the trade direction.
                elif tf_dir == 'long' and price <= vwap:
                    tf_block = 'price <= VWAP'
                elif tf_dir == 'short' and price >= vwap:
                    tf_block = 'price >= VWAP'

                if tf_block:
                    blocked_by = f'trend-pullback {tf_dir} blocked — {tf_block}'
                    log.info('%s %s', symbol, blocked_by)
                else:
                    signal, strategy = tf_dir, 'trend'
                    blocked_by = None
                    log.info('%s trend-pullback %s — regime below=%d above=%d sma=%s rsi=%.0f dev=%.2f',
                             symbol, tf_dir, _consec_below_vwap.get(symbol, 0),
                             _consec_above_vwap.get(symbol, 0), trend, rsi or 0, dev_pct or 0)

        orb_dir = check_orb_signal(price, orb_state, orb_end_min,
                                    STRATEGY_PARAMS['orb_min_range_ticks'], tick)
        if orb_dir:
            signal, strategy = orb_dir, 'orb'
            blocked_by = None

        # Exhaustion fade — fade a fresh extreme made on BELOW-average volume, using
        # the 1-min bars already fetched for the real VWAP. Fallback: only fires when
        # nothing else signaled this scan. The low-volume condition is the edge.
        if signal is None and ENABLE_EXHAUSTION_FADE:
            _ef_bars = _vwap_bars_cache.get(symbol)
            if _ef_bars:
                ef_dir = check_exhaustion_fade(
                    _ef_bars, dev_pct, rsi,
                    lookback=EXH_FADE_LOOKBACK_BARS,
                    vol_mult=EXH_FADE_VOL_MULT,
                    min_dev_pct=EXH_FADE_MIN_DEV_PCT,
                )
                if ef_dir:
                    signal, strategy = ef_dir, 'exh_fade'
                    blocked_by = None
                    log.info('%s exhaustion fade %s — fresh extreme on low volume (dev=%.2f%% rsi=%.0f)',
                             symbol, ef_dir, dev_pct or 0, rsi or 0)

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

        # Confidence is POLICY/DISPLAY only — it does NOT block trades.
        # User decision (2026-05-19): go back to the simpler hard-filter flow
        # that was working pre-confidence-gating. Confidence remains as a
        # visualization metric on the dashboard so user can monitor setup
        # quality, but doesn't gate entries.

        # Time-of-day block — applied last so the signal/blocked_by reflects the real reason
        if signal and hour_blocked:
            log.info('%s %s blocked — hour %02d:00 ET historically loses', symbol, signal, et_hour_now)
            blocked_by = f'hour {et_hour_now:02d}:00 ET (bad-hour block)'
            signal, strategy = None, None

        orb_hi = orb_state.high if orb_state._ready and orb_state.high != float('-inf') else None
        orb_lo = orb_state.low  if orb_state._ready and orb_state.low  != float('inf')  else None
        # Order flow (cumulative volume delta + book imbalance) + reference levels for the dashboard
        cvd = None
        imbalance = None
        if client is not None and hasattr(client, 'get_order_flow'):
            try:
                cvd = client.get_order_flow(symbol, 60)
            except Exception:
                cvd = None
        if client is not None and hasattr(client, 'get_book_imbalance'):
            try:
                imbalance = client.get_book_imbalance(symbol)
            except Exception:
                imbalance = None
        if symbol not in _ref_levels_cache and client is not None and hasattr(client, 'get_bars'):
            try:
                from bot.futures.levels import compute_reference_levels
                _ref_levels_cache[symbol] = compute_reference_levels(client, symbol)
            except Exception:
                _ref_levels_cache[symbol] = None
        levels = dict(_ref_levels_cache.get(symbol) or {})
        try:
            from bot.futures.levels import round_levels
            levels['round'] = round_levels(price, symbol, n=1)
        except Exception:
            pass

        status_map[symbol] = {
            'price':      price,
            'vwap':       round(vwap, 2) if vwap else None,
            'cvd':        round(cvd) if cvd is not None else None,
            'imbalance':  imbalance,
            'levels':     levels,
            'halted':     dd_halted,
            'dev_pct':    dev_pct,
            'threshold':  round((tuned_dev_long + tuned_dev_short) / 2, 4),
            'sma':        round(sma, 2) if sma else None,
            'rsi':        round(rsi, 1) if rsi else None,
            'trend':      trend,
            'consec_below_vwap': _consec_below_vwap.get(symbol, 0),
            'consec_above_vwap': _consec_above_vwap.get(symbol, 0),
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
        elif _dt in STRONG_TREND_DAY_TYPES:
            # Reversion has little edge on a strong-trend day — keep participation
            # minimal rather than forcing normal size into a non-reverting market.
            contracts = 1
            log.info('%s strong-trend day (%s) — flooring reversion size to 1 for %s',
                     symbol, _dt, symbol)
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
        # Per-symbol exposure cap — NQ moves more points per bar than ES, so cap it
        # at 1 contract to halve its swing (user kept NQ enabled, just smaller).
        sym_max = SYMBOL_MAX_CONTRACTS.get(symbol)
        if sym_max is not None and contracts > sym_max:
            log.info('%s size capped %d -> %d (per-symbol limit)', symbol, contracts, sym_max)
            contracts = sym_max

        if trading_paused:
            log.info('Trading paused — skipping entry for %s %s', symbol, signal)
            continue
        if dd_halted:
            log.warning('Drawdown halt active — skipping entry for %s %s', symbol, signal)
            continue

        # PERSISTENT REGIME GATE — blocks fighting a sustained one-sided move.
        # EXEMPT the reversion ('vwap') strategy: its bounce confirmation already
        # proves price stretched then turned back, so the gate shouldn't also veto
        # it — otherwise the bot can never short a real reversal in an uptrend (the
        # missed-winner watched on 2026-05-20). The gate still guards other strategies.
        PERSISTENT_REGIME_BARS = 6
        if strategy not in ('vwap', 'exh_fade'):
            if signal == 'long' and _consec_below_vwap.get(symbol, 0) >= PERSISTENT_REGIME_BARS:
                log.warning('%s long BLOCKED — price below VWAP for %d scans (persistent downtrend)',
                            symbol, _consec_below_vwap[symbol])
                continue
            if signal == 'short' and _consec_above_vwap.get(symbol, 0) >= PERSISTENT_REGIME_BARS:
                log.warning('%s short BLOCKED — price above VWAP for %d scans (persistent uptrend)',
                            symbol, _consec_above_vwap[symbol])
                continue

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

    # Persist real-time fills (from the User Hub) for the dashboard fills panel.
    if client is not None and hasattr(client, 'recent_fills'):
        try:
            set_setting(FUTURES_DB_PATH, 'recent_fills', _json.dumps(client.recent_fills(20)))
        except Exception:
            pass


def job_manage(client):
    if not _is_market_hours(has_realtime=client is not None):
        return
    try:
        prices = get_yf_prices(SYMBOLS, tradovate_client=client)
        sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
        manage_futures_positions(client, FUTURES_DB_PATH, current_prices=prices, sim=sim)
        if not sim:
            _cancel_orphan_orders(client)
    except Exception:
        log.exception('Manager error')


def _cancel_orphan_orders(client):
    """Cancel resting broker orders that don't belong to any open bot trade.

    A leftover stop/target (e.g. after a manual close or a missed reconciliation) can
    fill later and open an unwanted position. We cancel any working order whose id
    isn't a stop/target of a currently-open trade.
    """
    if not (hasattr(client, 'search_open_orders') and hasattr(client, 'cancel_order')):
        return
    try:
        working = client.search_open_orders()
    except Exception:
        return
    if not working:
        return
    from bot.futures.db import get_open_trades
    known = set()
    for t in get_open_trades(FUTURES_DB_PATH):
        for key in ('stop_order_id', 'target_order_id'):
            if t.get(key):
                known.add(str(t[key]))
    for o in working:
        oid = str(o.get('id') or o.get('orderId') or '')
        if oid and oid not in known:
            log.warning('Cancelling orphan working order %s (not tied to an open trade)', oid)
            try:
                client.cancel_order(oid)
            except Exception:
                pass


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


def job_backtest(client):
    """Nightly backtest over recent history; stores a summary for the dashboard."""
    if client is None or not hasattr(client, 'get_bars'):
        return
    try:
        import json as _json
        from bot.futures.backtest import fetch_history, run_backtest, sweep_dev_thresholds
        out = {'ts': datetime.now(ET).isoformat(), 'days': 10, 'symbols': {}}
        for sym in SYMBOLS:
            bars = fetch_history(client, sym, days=10)
            if not bars:
                continue
            res = run_backtest(bars, sym)
            by_strat = {}
            for st in ('vwap', 'exh_fade'):
                ts = [t for t in res['trades'] if t['strategy'] == st]
                if ts:
                    pnl = sum(t['pnl'] for t in ts); w = sum(1 for t in ts if t['pnl'] > 0)
                    by_strat[st] = {'n': len(ts), 'pnl': round(pnl), 'wr': round(w / len(ts) * 100)}
            out['symbols'][sym] = {
                'bars':  len(bars),
                'stats': res['stats'],
                'by_strategy': by_strat,
                'sweep': sweep_dev_thresholds(bars, sym),
            }
        set_setting(FUTURES_DB_PATH, 'backtest_results', _json.dumps(out))
        log.info('Backtest stored for %s', list(out['symbols'].keys()))
    except Exception:
        log.exception('Backtest job failed')


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
    def _backtest(): job_backtest(client)

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
    # Nightly backtest over recent history (stores a summary for the dashboard), plus
    # one run ~30s after startup so the panel has data right away.
    scheduler.add_job(_backtest, CronTrigger(hour=17, minute=15, timezone=ET), id='backtest')
    scheduler.add_job(_backtest, 'date',
                      run_date=datetime.now(ET) + timedelta(seconds=30), id='backtest_startup')
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
