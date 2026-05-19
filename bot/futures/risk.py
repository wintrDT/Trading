# bot/futures/risk.py
from datetime import datetime, timezone


def calc_stop_price(direction: str, entry: float, stop_ticks: int, tick: float) -> float:
    offset = round(stop_ticks * tick, 4)
    return round(entry - offset if direction == 'long' else entry + offset, 4)


def calc_target_price(direction: str, entry: float, target_ticks: int, tick: float) -> float:
    offset = round(target_ticks * tick, 4)
    return round(entry + offset if direction == 'long' else entry - offset, 4)


def calc_pnl(direction: str, entry: float, close: float, contracts: int, point_value: float) -> float:
    points = close - entry if direction == 'long' else entry - close
    return round(points * contracts * point_value, 2)


def is_daily_loss_limit_hit(realized: float, limit: float) -> bool:
    return realized <= -abs(limit)


def is_news_blackout(now_iso: str, blackout_minutes: int, news_times: list) -> bool:
    now_dt = datetime.fromisoformat(now_iso.replace('Z', '+00:00'))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    for nt in news_times:
        event_dt = datetime.fromisoformat(nt.replace('Z', '+00:00'))
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        if abs((now_dt - event_dt).total_seconds()) / 60 <= blackout_minutes:
            return True
    return False


def news_regime(now_iso: str, news_times: list,
                blackout_min: int = 5, near_min: int = 15) -> dict:
    """Volatility engine for scheduled news events.

    Returns a dict:
        {'state': 'pause'|'near_event'|'normal', 'minutes_to': float|None}

    - 'pause'      — within blackout_min of an event (hard skip trades)
    - 'near_event' — within near_min of an event (apply sizing/stop multipliers)
    - 'normal'     — no event nearby
    """
    if not news_times:
        return {'state': 'normal', 'minutes_to': None}
    now_dt = datetime.fromisoformat(now_iso.replace('Z', '+00:00'))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    deltas = []
    for nt in news_times:
        ev_dt = datetime.fromisoformat(nt.replace('Z', '+00:00'))
        if ev_dt.tzinfo is None:
            ev_dt = ev_dt.replace(tzinfo=timezone.utc)
        deltas.append((ev_dt - now_dt).total_seconds() / 60.0)
    closest = min(deltas, key=abs)
    a = abs(closest)
    if a <= blackout_min:
        return {'state': 'pause',      'minutes_to': closest}
    if a <= near_min:
        return {'state': 'near_event', 'minutes_to': closest}
    return {'state': 'normal', 'minutes_to': None}


def should_exit(direction: str, current_price: float, stop_price: float, target_price: float) -> str | None:
    if direction == 'long':
        if current_price <= stop_price:
            return 'stop_loss'
        if current_price >= target_price:
            return 'profit_target'
    else:
        if current_price >= stop_price:
            return 'stop_loss'
        if current_price <= target_price:
            return 'profit_target'
    return None


def calc_trailing_stop(direction: str, entry: float, current_price: float,
                        tick: float, trigger_ticks: int = 8, trail_ticks: int = 4) -> float | None:
    """Once price moves trigger_ticks in our favor, trail stop trail_ticks behind current price."""
    trigger = trigger_ticks * tick
    trail   = trail_ticks * tick
    if direction == 'long' and current_price >= entry + trigger:
        return round(current_price - trail, 4)
    if direction == 'short' and current_price <= entry - trigger:
        return round(current_price + trail, 4)
    return None


def calc_breakeven_stop(direction: str, entry: float, current_price: float,
                         target_price: float, trigger_pct: float = 0.25) -> float | None:
    """Once price reaches trigger_pct of target, return a breakeven stop price."""
    if direction == 'long':
        trigger = entry + (target_price - entry) * trigger_pct
        if current_price >= trigger:
            return entry
    else:
        trigger = entry - (entry - target_price) * trigger_pct
        if current_price <= trigger:
            return entry
    return None
