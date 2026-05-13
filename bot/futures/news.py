# bot/futures/news.py
import logging
from datetime import datetime, timezone
import httpx

log = logging.getLogger(__name__)

HIGH_IMPACT_KEYWORDS = {
    'FOMC', 'Federal Reserve', 'Fed Rate', 'Interest Rate Decision',
    'CPI', 'Consumer Price Index', 'Inflation',
    'NFP', 'Non-Farm Payroll', 'Nonfarm Payroll', 'Jobs Report',
    'GDP', 'Gross Domestic Product',
    'PMI', 'ISM',
    'Retail Sales', 'PCE', 'PPI',
    'Unemployment Rate', 'Initial Jobless',
}

_NASDAQ_URL = 'https://api.nasdaq.com/api/calendar/economicevents'
_NASDAQ_HEADERS = {'Accept': 'application/json, text/plain, */*',
                   'User-Agent': 'Mozilla/5.0'}


def _fetch_nasdaq_events(date_str: str) -> list:
    try:
        resp = httpx.get(
            _NASDAQ_URL,
            params={'date': date_str},
            headers=_NASDAQ_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', {}).get('rows', [])
    except Exception as exc:
        log.warning('Nasdaq calendar fetch failed: %s', exc)
        return []


def parse_econ_events(raw_rows: list, date_str: str) -> list:
    """Filter raw Nasdaq calendar rows to high-impact events only."""
    events = []
    for row in raw_rows:
        impact = row.get('impact', '')
        if impact != 'High':
            continue
        desc = row.get('description', '') or row.get('eventName', '')
        time_str = (row.get('time', '08:30') or '08:30').strip()
        try:
            event_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        except ValueError:
            event_dt = datetime.fromisoformat(f"{date_str}T08:30:00")
        events.append({
            'news_type': 'event',
            'title':     desc,
            'event_ts':  event_dt.isoformat(),
            'impact':    impact,
            'sentiment': None,
            'url':       None,
        })
    return events


def _fetch_av_headlines(api_key: str, tickers='SPY,QQQ,ES', limit=5) -> dict:
    try:
        resp = httpx.get(
            'https://www.alphavantage.co/query',
            params={
                'function': 'NEWS_SENTIMENT',
                'tickers':  tickers,
                'limit':    limit,
                'apikey':   api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning('Alpha Vantage news fetch failed: %s', exc)
        return {}


def parse_av_headlines(raw: dict, limit=5) -> list:
    headlines = []
    for item in raw.get('feed', [])[:limit]:
        tp = item.get('time_published', '')
        try:
            event_dt = datetime.strptime(tp, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
            event_iso = event_dt.isoformat()
        except ValueError:
            event_iso = datetime.now(timezone.utc).isoformat()
        headlines.append({
            'news_type': 'headline',
            'title':     item.get('title', ''),
            'event_ts':  event_iso,
            'impact':    None,
            'sentiment': item.get('overall_sentiment_label', 'Neutral'),
            'url':       item.get('url', ''),
        })
    return headlines


def fetch_and_store_news(db_path: str, av_api_key: str):
    """Fetch economic events + headlines and write to DB. Called hourly."""
    from bot.futures.db import upsert_news
    now_iso  = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    items = []

    raw_events = _fetch_nasdaq_events(date_str)
    events     = parse_econ_events(raw_events, date_str)
    for e in events:
        e['fetched_ts'] = now_iso
    items.extend(events)
    log.info('Fetched %d high-impact economic events for %s', len(events), date_str)

    if av_api_key:
        raw_av    = _fetch_av_headlines(av_api_key, limit=5)
        headlines = parse_av_headlines(raw_av, limit=5)
        for h in headlines:
            h['fetched_ts'] = now_iso
        items.extend(headlines)
        log.info('Fetched %d news headlines', len(headlines))

    if items:
        upsert_news(db_path, items)
