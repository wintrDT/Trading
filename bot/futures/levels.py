# bot/futures/levels.py
"""Session reference levels: prior-day RTH high/low/close, overnight (Globex)
high/low, and nearest round numbers.

These are the price levels where reactions cluster (stops, breakouts, fades) and
power the sweep-reversal strategy + the dashboard. Computed from 1-min bars.
The segmentation logic (session_levels, round_levels) is pure and unit-tested;
compute_reference_levels wires it to the broker's bar history.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
_RTH_START_MIN = 9 * 60 + 30    # 09:30 ET
_RTH_END_MIN   = 16 * 60        # 16:00 ET
ROUND_STEP = {'ES': 25.0, 'NQ': 100.0, 'GC': 10.0}


def _et(ts_iso):
    return datetime.fromisoformat(str(ts_iso).replace('Z', '+00:00')).astimezone(ET)


def _is_rth(dt) -> bool:
    mins = dt.hour * 60 + dt.minute
    return _RTH_START_MIN <= mins < _RTH_END_MIN


def round_levels(price: float, symbol: str, n: int = 1) -> list:
    """Nearest round-number levels (ES every 25, NQ every 100, GC every 10), n each side."""
    step = ROUND_STEP.get(symbol, 25.0)
    base = round(price / step) * step
    return sorted({round(base + i * step, 2) for i in range(-n, n + 1)})


def session_levels(bars: list) -> dict:
    """From chronological 1-min bars (>= ~1.5 sessions) compute prior-day RTH
    high/low/close and the most recent overnight (Globex) high/low.

    Returns a dict with None for any piece not derivable from the bars.
    """
    rth = {}        # ET-date -> list of (high, low, close, dt)
    overnight = {}  # morning-ET-date -> list of (high, low)
    for b in bars:
        try:
            dt = _et(b['t']); h = float(b['h']); l = float(b['l']); c = float(b['c'])
        except (KeyError, TypeError, ValueError):
            continue
        if _is_rth(dt):
            rth.setdefault(dt.strftime('%Y-%m-%d'), []).append((h, l, c, dt))
        else:
            # overnight bar belongs to the morning it precedes:
            # 00:00-09:29 ET -> same day; 18:00-23:59 ET -> next day
            mins = dt.hour * 60 + dt.minute
            morning = dt if mins < _RTH_START_MIN else dt + timedelta(days=1)
            overnight.setdefault(morning.strftime('%Y-%m-%d'), []).append((h, l))

    out = {'prior_day_high': None, 'prior_day_low': None, 'prior_day_close': None,
           'overnight_high': None, 'overnight_low': None}

    # prior day = latest COMPLETE RTH session (one that has a bar at/after 15:00 ET)
    complete = [d for d, rows in rth.items() if any(r[3].hour >= 15 for r in rows)]
    if complete:
        rows = rth[max(complete)]
        out['prior_day_high'] = max(r[0] for r in rows)
        out['prior_day_low'] = min(r[1] for r in rows)
        out['prior_day_close'] = sorted(rows, key=lambda r: r[3])[-1][2]

    if overnight:
        rows = overnight[max(overnight.keys())]
        out['overnight_high'] = max(r[0] for r in rows)
        out['overnight_low'] = min(r[1] for r in rows)

    return out


def compute_reference_levels(client, symbol: str, days: int = 3) -> dict:
    """Fetch recent 1-min bars and compute session levels (live integration)."""
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
    raw.sort(key=lambda x: str(x.get('t')))
    return session_levels(raw)
