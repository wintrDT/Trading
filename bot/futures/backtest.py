# bot/futures/backtest.py
"""Historical backtest harness for the futures strategies.

Replays 1-min TopStep bars through the same indicator state + signal functions the
live bot uses (real volume-weighted VWAP, RSI, ATR, ORB, exhaustion fade, and the
bounce-confirmed VWAP reversion) and simulates entries/exits with the live stop /
target / timeout rules. Lets us validate strategies and sweep deviation thresholds
on months of data BEFORE risking the account.

Note: this models the core entry edges (reversion deviation+RSI, exhaustion fade,
ORB). It does NOT replay the soft live gates (news, day-type sizing, hour blocks,
trend strategy) — it answers "does the core edge hold + what threshold is best",
not "exact live P&L". Fills are conservative (stop fills at stop, target at target,
stop checked before target within a bar).
"""
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from bot.futures.config import TICK_INFO, SYMBOL_VWAP_PCT, RISK_RULES, SYMBOL_RISK, STRATEGY_PARAMS
from bot.futures.strategy import (
    VWAPState, calc_vwap, RSIState, SMAState, VolatilityState,
    check_exhaustion_fade,
)
from bot.futures.risk import calc_trailing_stop, calc_breakeven_stop

log = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


def fetch_history(client, symbol: str, days: int = 14) -> list:
    """Page 1-min bars back `days` days (chronological, de-duped). Reuses get_bars."""
    end = datetime.now(timezone.utc)
    raw = []
    for d in range(days):
        win_end = end - timedelta(days=d)
        win_start = win_end - timedelta(days=1)
        bars = client.get_bars(symbol,
                               win_start.isoformat().replace('+00:00', 'Z'),
                               win_end.isoformat().replace('+00:00', 'Z'),
                               limit=1500)
        raw.extend(bars or [])
    seen, uniq = set(), []
    for b in sorted(raw, key=lambda x: str(x.get('t'))):
        t = str(b.get('t'))
        if t in seen:
            continue
        seen.add(t)
        uniq.append(b)
    return uniq


def _session_key(ts_iso: str) -> str:
    """Session id anchored to 18:00 ET (a bar at/after 18:00 ET belongs to the next day's session)."""
    dt = datetime.fromisoformat(ts_iso.replace('Z', '+00:00')).astimezone(ET)
    anchored = dt + timedelta(hours=6)   # shift so 18:00 -> 00:00
    return anchored.strftime('%Y-%m-%d')


class _Reversion:
    """Local copy of the live bounce-confirmed reversion entry (no module globals)."""
    def __init__(self, retrace=0.25, retrace_cap_pct=0.10):
        self.retrace = retrace
        self.cap = retrace_cap_pct
        self.state = None  # {'side','peak'}

    def reset(self):
        self.state = None

    def _confirmed(self, side, peak, dev):
        trigger = min(self.retrace * abs(peak), self.cap)
        retraced = (dev - peak) if side == 'long' else (peak - dev)
        return retraced >= trigger

    def check(self, dev, thresh_long, thresh_short):
        s = self.state
        if s and ((s['side'] == 'long' and dev >= 0) or (s['side'] == 'short' and dev <= 0)):
            self.state = s = None
        if dev <= -thresh_long:
            if s is None or s['side'] != 'long':
                self.state = {'side': 'long', 'peak': dev}; return None
            if dev < s['peak']:
                s['peak'] = dev; return None
            if self._confirmed('long', s['peak'], dev):
                self.state = None; return 'long'
            return None
        if dev >= thresh_short:
            if s is None or s['side'] != 'short':
                self.state = {'side': 'short', 'peak': dev}; return None
            if dev > s['peak']:
                s['peak'] = dev; return None
            if self._confirmed('short', s['peak'], dev):
                self.state = None; return 'short'
            return None
        if s and self._confirmed(s['side'], s['peak'], dev):
            d = s['side']; self.state = None; return d
        return None


def _stops_for(symbol, direction, entry, atr, tick):
    """Mirror place_entry: fixed stop/target ticks (per-symbol) with ATR-adaptive stop."""
    risk = SYMBOL_RISK.get(symbol, RISK_RULES)
    if direction == 'short':
        stop_ticks = risk.get('short_stop_ticks', risk.get('stop_ticks', 8))
        target_ticks = risk.get('short_target_ticks', risk.get('target_ticks', 16))
    else:
        stop_ticks = risk.get('stop_ticks', 8)
        target_ticks = risk.get('target_ticks', 16)
    if atr and atr > 0:
        atr_stop = round(atr * 2.5 / tick)
        stop_ticks = max(max(4, stop_ticks // 2), min(int(stop_ticks * 2.0), atr_stop))
    stop = entry - stop_ticks * tick if direction == 'long' else entry + stop_ticks * tick
    target = entry + target_ticks * tick if direction == 'long' else entry - target_ticks * tick
    return round(stop, 4), round(target, 4)


def run_backtest(bars: list, symbol: str, dev_long=None, dev_short=None,
                 enable_exhaustion=True, timeout_min=30) -> dict:
    """Replay bars; return {'trades': [...], 'stats': {...}} for the given thresholds."""
    tick = TICK_INFO.get(symbol, TICK_INFO['ES'])['tick']
    pv = TICK_INFO.get(symbol, TICK_INFO['ES'])['point_value']
    base_thresh = SYMBOL_VWAP_PCT.get(symbol, STRATEGY_PARAMS['vwap_deviation_pct'])
    dev_long = dev_long if dev_long is not None else base_thresh
    dev_short = dev_short if dev_short is not None else base_thresh

    vwap = VWAPState(); rsi = RSIState(); sma = SMAState(); vol = VolatilityState()
    rev = _Reversion()
    cur_session = None
    recent = []        # rolling window of bars for exhaustion fade
    pos = None         # open position dict
    trades = []

    for b in bars:
        try:
            o, h, l, c = float(b['o']), float(b['h']), float(b['l']), float(b['c'])
            v = float(b.get('v', 0) or 0)
            t = str(b['t'])
        except (KeyError, TypeError, ValueError):
            continue

        sk = _session_key(t)
        if sk != cur_session:
            cur_session = sk
            vwap = VWAPState(); rev.reset()   # session VWAP + reversion reset at 18:00 ET

        # --- manage an open position against THIS bar's range (before updating signals) ---
        if pos:
            hit = None; fill = None
            if pos['dir'] == 'long':
                if l <= pos['stop']: hit, fill = 'stop', pos['stop']
                elif h >= pos['target']: hit, fill = 'target', pos['target']
            else:
                if h >= pos['stop']: hit, fill = 'stop', pos['stop']
                elif l <= pos['target']: hit, fill = 'target', pos['target']
            age_min = (datetime.fromisoformat(t.replace('Z', '+00:00')) -
                       datetime.fromisoformat(pos['entry_ts'].replace('Z', '+00:00'))).total_seconds() / 60
            if not hit and age_min >= timeout_min:
                hit, fill = 'timeout', c
            if hit:
                pnl = (fill - pos['entry'] if pos['dir'] == 'long' else pos['entry'] - fill) * pv
                trades.append({'symbol': symbol, 'strategy': pos['strategy'], 'dir': pos['dir'],
                               'entry': pos['entry'], 'exit': fill, 'reason': hit,
                               'pnl': round(pnl, 2), 'entry_ts': pos['entry_ts'], 'dev': pos['dev']})
                pos = None
            else:
                # Ratchet the stop with breakeven + trailing (mirrors the live manager,
                # the core profit driver) using this bar's favorable extreme; applies
                # to subsequent bars. Approximation at 1-min granularity.
                fav = h if pos['dir'] == 'long' else l
                be = calc_breakeven_stop(pos['dir'], pos['entry'], fav, tick)
                tr = calc_trailing_stop(pos['dir'], pos['entry'], fav, tick)
                for cand in (be, tr):
                    if cand is None:
                        continue
                    if pos['dir'] == 'long' and cand > pos['stop']:
                        pos['stop'] = cand
                    elif pos['dir'] == 'short' and cand < pos['stop']:
                        pos['stop'] = cand

        # --- update indicator states with this completed bar ---
        typical = (h + l + c) / 3.0
        if v > 0:
            vwap.add_bar(price=typical, volume=v)
        sma.update(c); rsi.update(c); vol.update(c); vol.update_baseline(c)
        recent.append(b)
        if len(recent) > 60:
            recent = recent[-60:]

        vw = calc_vwap(vwap)
        if vw is None or vw == 0:
            continue
        dev = (c - vw) / vw * 100.0
        rv = rsi.value(); atr = vol.atr()

        # --- look for a new entry only when flat ---
        if pos:
            continue
        sig = None; strat = None
        d = rev.check(dev, dev_long, dev_short)
        # apply the live reversion RSI gate (block shorts with RSI >= 60)
        if d == 'short' and rv is not None and rv >= RISK_RULES.get('reversion_rsi_short_max', 60):
            d = None
        if d:
            sig, strat = d, 'vwap'
        elif enable_exhaustion:
            ef = check_exhaustion_fade(recent, dev, rv,
                                       lookback=20, vol_mult=0.7, min_dev_pct=0.10)
            if ef:
                sig, strat = ef, 'exh_fade'
        if sig:
            stop, target = _stops_for(symbol, sig, c, atr, tick)
            pos = {'dir': sig, 'strategy': strat, 'entry': c, 'stop': stop, 'target': target,
                   'entry_ts': t, 'dev': round(dev, 4)}

    return {'trades': trades, 'stats': _stats(trades)}


def _stats(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {'n': 0, 'pnl': 0.0, 'win_rate': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0, 'expectancy': 0.0}
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] < 0]
    pnl = sum(t['pnl'] for t in trades)
    return {
        'n': n, 'pnl': round(pnl, 2),
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_win': round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0.0,
        'avg_loss': round(sum(t['pnl'] for t in losses) / len(losses), 2) if losses else 0.0,
        'expectancy': round(pnl / n, 2),
    }


def sweep_dev_thresholds(bars: list, symbol: str, candidates=(0.03, 0.05, 0.08, 0.10, 0.15)) -> list:
    """Run the backtest at several deviation thresholds; return stats per threshold (recalibration)."""
    out = []
    for th in candidates:
        res = run_backtest(bars, symbol, dev_long=th, dev_short=th, enable_exhaustion=False)
        s = res['stats']; s['threshold'] = th
        out.append(s)
    return out
