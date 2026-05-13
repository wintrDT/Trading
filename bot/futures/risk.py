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
