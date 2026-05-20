# bot/futures/news.py
import logging
from datetime import datetime, timezone
import httpx
import pytz
import yfinance as yf

ET = pytz.timezone('America/New_York')

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

def _fetch_finnhub_events(api_key: str, date_str: str) -> list:
    """Fetch US high-impact economic events from Finnhub calendar."""
    if not api_key:
        return []
    try:
        resp = httpx.get(
            'https://finnhub.io/api/v1/calendar/economic',
            params={'token': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        events = []
        for e in resp.json().get('economicCalendar', []):
            if e.get('country') != 'US':
                continue
            if e.get('impact') != 'high':
                continue
            ts = e.get('time', '')
            if not ts.startswith(date_str):
                continue
            try:
                event_dt_utc = datetime.fromisoformat(ts.replace(' ', 'T')).replace(tzinfo=timezone.utc)
                event_dt_et  = event_dt_utc.astimezone(ET)
            except ValueError:
                continue
            events.append({
                'news_type': 'event',
                'title':     e.get('event', ''),
                'event_ts':  event_dt_et.isoformat(),
                'impact':    'High',
                'sentiment': None,
                'url':       None,
            })
        return events
    except Exception as exc:
        log.warning('Finnhub calendar fetch failed: %s', exc)
        return []


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


_BULLISH_WORDS = {
    'surges', 'jumps', 'gains', 'beats', 'strong', 'growth', 'rally', 'rises',
    'bull', 'record', 'high', 'upgrade', 'buy', 'positive', 'boom', 'soars',
    'rebounds', 'recovery', 'expands', 'outperforms', 'optimism', 'upbeat',
}
_BEARISH_WORDS = {
    'falls', 'drops', 'slumps', 'misses', 'weak', 'decline', 'selloff', 'retreats',
    'bear', 'low', 'downgrade', 'sell', 'negative', 'recession', 'plunges', 'slides',
    'worries', 'fears', 'disappoints', 'contracts', 'underperforms', 'concern',
}


def _score_headline(title: str) -> str:
    words = set(title.lower().split())
    bull = len(words & _BULLISH_WORDS)
    bear = len(words & _BEARISH_WORDS)
    if bull > bear:
        return 'Bullish'
    if bear > bull:
        return 'Bearish'
    return 'Neutral'


def _fetch_yf_headlines() -> list:
    """Free market news from Yahoo Finance — no API key needed."""
    items = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for sym in ['SPY', 'QQQ']:
        try:
            news = yf.Ticker(sym).news or []
            for article in news[:4]:
                # yfinance wraps content under a 'content' key in newer versions
                content = article.get('content', article)
                title = content.get('title', '')
                if not title:
                    continue
                pub_str = content.get('pubDate', '') or content.get('displayTime', '')
                try:
                    event_iso = datetime.strptime(pub_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).isoformat()
                except Exception:
                    event_iso = now_iso
                url = (content.get('canonicalUrl') or {}).get('url', '') or (content.get('clickThroughUrl') or {}).get('url', '')
                # Score on title + summary for better accuracy
                text = title + ' ' + content.get('summary', '')
                items.append({
                    'news_type': 'headline',
                    'title':     title,
                    'event_ts':  event_iso,
                    'impact':    None,
                    'sentiment': _score_headline(text),
                    'url':       url,
                })
        except Exception as exc:
            log.warning('yfinance news fetch failed for %s: %s', sym, exc)
    # deduplicate by title
    seen, unique = set(), []
    for item in items:
        if item['title'] not in seen:
            seen.add(item['title'])
            unique.append(item)
    return unique


def _fetch_finnhub_news(api_key: str) -> list:
    """Real-time market news from Finnhub — replaces yfinance as primary source."""
    if not api_key:
        return []
    items    = []
    now_iso  = datetime.now(timezone.utc).isoformat()
    today    = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # General market news (covers macro, Fed, inflation)
    try:
        resp = httpx.get(
            'https://finnhub.io/api/v1/news',
            params={'category': 'general', 'token': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        for art in resp.json()[:10]:
            title = art.get('headline', '')
            if not title:
                continue
            try:
                ts = datetime.utcfromtimestamp(art['datetime']).replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                ts = now_iso
            text = title + ' ' + art.get('summary', '')
            items.append({
                'news_type': 'headline', 'title': title,
                'event_ts': ts, 'impact': None,
                'sentiment': _score_headline(text), 'url': art.get('url', ''),
            })
    except Exception as exc:
        log.warning('Finnhub general news failed: %s', exc)

    # Company news for ES/NQ/GC proxies (SPY, QQQ, GLD)
    for sym in ['SPY', 'QQQ', 'GLD']:
        try:
            resp = httpx.get(
                'https://finnhub.io/api/v1/company-news',
                params={'symbol': sym, 'from': today, 'to': today, 'token': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            for art in resp.json()[:3]:
                title = art.get('headline', '')
                if not title:
                    continue
                try:
                    ts = datetime.utcfromtimestamp(art['datetime']).replace(tzinfo=timezone.utc).isoformat()
                except Exception:
                    ts = now_iso
                text = title + ' ' + art.get('summary', '')
                items.append({
                    'news_type': 'headline', 'title': title,
                    'event_ts': ts, 'impact': None,
                    'sentiment': _score_headline(text), 'url': art.get('url', ''),
                })
        except Exception as exc:
            log.warning('Finnhub %s news failed: %s', sym, exc)

    seen, unique = set(), []
    for item in items:
        if item['title'] not in seen:
            seen.add(item['title'])
            unique.append(item)
    log.info('Fetched %d Finnhub headlines', len(unique))
    return unique


def fetch_and_store_news(db_path: str, av_api_key: str, finnhub_api_key: str = '', yf_only: bool = False):
    """Fetch headlines and store in DB. Uses Finnhub when key provided, yfinance as fallback."""
    from bot.futures.db import upsert_news
    now_iso  = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    items    = []

    if not yf_only:
        events = _fetch_finnhub_events(finnhub_api_key, date_str)
        for e in events:
            e['fetched_ts'] = now_iso
        items.extend(events)
        log.info('Fetched %d US high-impact events for %s via Finnhub', len(events), date_str)

        if av_api_key:
            raw_av    = _fetch_av_headlines(av_api_key, limit=5)
            headlines = parse_av_headlines(raw_av, limit=5)
            for h in headlines:
                h['fetched_ts'] = now_iso
            items.extend(headlines)
            log.info('Fetched %d AV headlines', len(headlines))

    # Finnhub is real-time — use it when available, fall back to yfinance
    if finnhub_api_key:
        fh = _fetch_finnhub_news(finnhub_api_key)
        for h in fh:
            h['fetched_ts'] = now_iso
        items.extend(fh)
    else:
        yf_headlines = _fetch_yf_headlines()
        for h in yf_headlines:
            h['fetched_ts'] = now_iso
        items.extend(yf_headlines)
        log.info('Fetched %d yfinance headlines', len(yf_headlines))

    if items:
        upsert_news(db_path, items)
