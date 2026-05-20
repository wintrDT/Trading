"""
Fetch high-impact US economic events from the Nasdaq economic calendar.
Results are cached per trading day so we only hit the API once per scan cycle.
"""
import logging
from datetime import date, timedelta

import httpx

log = logging.getLogger(__name__)

_NASDAQ_URL = "https://api.nasdaq.com/api/calendar/economicevents"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}

_cache: dict = {}
_cache_date: date | None = None


def _fetch_week(week_date: date) -> set[date]:
    try:
        resp = httpx.get(
            _NASDAQ_URL,
            params={"date": week_date.isoformat()},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = (data.get("data") or {}).get("rows") or []
        result = set()
        for r in rows:
            if str(r.get("importance", "")).lower() != "high":
                continue
            country = str(r.get("country", ""))
            if "united states" not in country.lower() and country.upper() != "US":
                continue
            raw = r.get("date") or r.get("eventDate") or r.get("releaseDate")
            if not raw:
                continue
            try:
                result.add(date.fromisoformat(str(raw)[:10]))
            except ValueError:
                pass
        return result
    except Exception as exc:
        log.warning("Economic calendar fetch failed for week %s: %s", week_date, exc)
        return set()


def high_impact_dates_between(from_date: date, to_date: date) -> set[date]:
    global _cache, _cache_date
    today = date.today()
    if _cache_date != today:
        _cache.clear()
        _cache_date = today

    key = (from_date, to_date)
    if key in _cache:
        return _cache[key]

    events: set[date] = set()
    cursor = from_date
    while cursor <= to_date:
        events |= _fetch_week(cursor)
        cursor += timedelta(weeks=1)

    result = {d for d in events if from_date <= d <= to_date}
    _cache[key] = result
    return result


def straddles_high_impact(exp_date: date, today: date | None = None) -> bool:
    if today is None:
        today = date.today()
    return bool(high_impact_dates_between(today + timedelta(days=1), exp_date))
