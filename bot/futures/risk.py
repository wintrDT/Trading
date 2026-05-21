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


def trailing_floor(peak: float, trailing: float, start: float) -> float:
    """TopStep trailing max-loss floor: `trailing` below the peak, but it stops
    trailing (locks) once it reaches the starting balance — so it never rises above
    `start`. e.g. 50k start, $2000 trailing: peak 50k -> 48k, peak 51k -> 49k,
    peak 52k+ -> locked at 50k.
    """
    return min(peak - trailing, start)


def drawdown_halt_decision(net_liq: float, peak: float, can_trade: bool,
                           start: float, trailing: float, buffer: float,
                           profit_target: float = 0.0):
    """Pure decision: should the bot HALT new entries? Returns (halt, reason).

    Halts if the account is locked, if equity is within `buffer` of the trailing
    floor (so we stop BEFORE breaching), or if the Combine profit target is reached.
    """
    if not can_trade:
        return True, f'account locked (canTrade=False, balance=${net_liq:.0f})'
    floor = trailing_floor(peak, trailing, start)
    if net_liq <= floor + buffer:
        return True, (f'within ${buffer:.0f} of trailing floor '
                      f'(balance=${net_liq:.0f}, floor=${floor:.0f}, peak=${peak:.0f})')
    if profit_target and net_liq >= start + profit_target:
        return True, f'Combine profit target reached (+${net_liq - start:.0f}) — banking it'
    return False, None


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
                        tick: float, trigger_ticks: int = 12, trail_ticks: int = 8) -> float | None:
    """Once price moves trigger_ticks in our favor, trail stop trail_ticks behind current price.

    Trail is kept wider than the typical 1-2pt noise band so a normal pullback
    doesn't shake the trade out before the move develops.
    """
    trigger = trigger_ticks * tick
    trail   = trail_ticks * tick
    if direction == 'long' and current_price >= entry + trigger:
        return round(current_price - trail, 4)
    if direction == 'short' and current_price <= entry - trigger:
        return round(current_price + trail, 4)
    return None


def calc_breakeven_stop(direction: str, entry: float, current_price: float,
                         tick: float, trigger_ticks: int = 10, lock_ticks: int = 2) -> float | None:
    """Once price moves trigger_ticks in our favor, lock a small profit (lock_ticks past entry).

    Trigger is measured in ticks (not % of target) and sits above the noise band,
    so a 1-2pt wiggle no longer snaps the stop to entry and scratches the trade.
    Locking lock_ticks past entry means a breakeven stop-out is a tiny win that
    covers fees rather than a flat scratch.
    """
    trigger = trigger_ticks * tick
    lock    = lock_ticks * tick
    if direction == 'long' and current_price >= entry + trigger:
        return round(entry + lock, 4)
    if direction == 'short' and current_price <= entry - trigger:
        return round(entry - lock, 4)
    return None
