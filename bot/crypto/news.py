# bot/crypto/news.py
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx

log = logging.getLogger(__name__)

_RSS_FEEDS = [
    'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'https://cointelegraph.com/rss',
    'https://decrypt.co/feed',
]

_FNG_URL = 'https://api.alternative.me/fng/?limit=1'

_BULLISH_WORDS = {
    'surges', 'jumps', 'gains', 'beats', 'strong', 'growth', 'rally', 'rises',
    'bull', 'record', 'upgrade', 'buy', 'positive', 'boom', 'soars', 'rebounds',
    'recovery', 'expands', 'optimism', 'upbeat', 'breakout', 'adoption', 'launch',
    'partnership', 'approval', 'etf', 'accumulate', 'milestone',
}
_BEARISH_WORDS = {
    'falls', 'drops', 'slumps', 'misses', 'weak', 'decline', 'selloff', 'retreats',
    'bear', 'downgrade', 'sell', 'negative', 'recession', 'plunges', 'slides',
    'worries', 'fears', 'disappoints', 'crash', 'ban', 'hack', 'exploit', 'fine',
    'probe', 'lawsuit', 'suspend', 'halt', 'warning', 'fraud',
}

# Which coins each RSS item likely relates to
_COIN_KEYWORDS = {
    'BTCUSDT':  {'bitcoin', 'btc'},
    'ETHUSDT':  {'ethereum', 'eth', 'ether'},
    'SOLUSDT':  {'solana', 'sol'},
    'DOGEUSDT': {'dogecoin', 'doge'},
}


def _score_title(title: str) -> tuple[str, float]:
    words = set(title.lower().split())
    bull  = len(words & _BULLISH_WORDS)
    bear  = len(words & _BEARISH_WORDS)
    if bull > bear:
        return 'Bullish', 1.0
    if bear > bull:
        return 'Bearish', -1.0
    return 'Neutral', 0.0


def _symbols_from_text(text: str) -> list[str]:
    lower = text.lower()
    matched = [sym for sym, kws in _COIN_KEYWORDS.items() if any(k in lower for k in kws)]
    return matched if matched else ['ALL']


def _fetch_rss() -> list:
    items    = []
    now_iso  = datetime.now(timezone.utc).isoformat()

    for url in _RSS_FEEDS:
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True,
                             headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns   = {'atom': 'http://www.w3.org/2005/Atom'}

            # Handle both RSS 2.0 (<item>) and Atom (<entry>)
            entries = root.findall('.//item') or root.findall('.//atom:entry', ns)
            for entry in entries[:8]:
                title_el = entry.find('title')
                title    = (title_el.text or '').strip() if title_el is not None else ''
                if not title:
                    continue

                pub_el = entry.find('pubDate') or entry.find('atom:published', ns)
                try:
                    pub_iso = parsedate_to_datetime(pub_el.text).isoformat() if pub_el is not None else now_iso
                except Exception:
                    pub_iso = now_iso

                link_el = entry.find('link')
                url_val = (link_el.text or '').strip() if link_el is not None else ''

                desc_el = entry.find('description') or entry.find('atom:summary', ns)
                desc    = (desc_el.text or '') if desc_el is not None else ''
                text    = title + ' ' + desc

                sentiment, score = _score_title(text)
                symbols          = _symbols_from_text(text)

                for sym in symbols:
                    items.append({
                        'fetched_ts':   now_iso,
                        'symbol':       sym,
                        'title':        title,
                        'published_at': pub_iso,
                        'sentiment':    sentiment,
                        'score':        score,
                        'url':          url_val,
                    })
        except Exception as exc:
            log.warning('RSS fetch failed for %s: %s', url, exc)

    return items


def _fetch_fear_and_greed() -> list:
    """Fear & Greed Index from alternative.me — free, no key, reflects overall crypto sentiment."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        resp = httpx.get(_FNG_URL, timeout=10)
        resp.raise_for_status()
        data  = resp.json().get('data', [{}])[0]
        value = int(data.get('value', 50))
        label = data.get('value_classification', 'Neutral')

        # Only act on extremes — normal fear/greed is just market noise
        if value <= 20:
            sentiment, score = 'Bullish', 1.0    # Extreme Fear — market likely oversold
        elif value >= 80:
            sentiment, score = 'Bearish', -1.0   # Extreme Greed — market likely overextended
        else:
            sentiment, score = 'Neutral', 0.0    # Everything else: don't interfere

        title = f'Fear & Greed Index: {value} ({label})'
        log.info(title)
        return [{
            'fetched_ts':   now_iso,
            'symbol':       'ALL',
            'title':        title,
            'published_at': now_iso,
            'sentiment':    sentiment,
            'score':        score,
            'url':          'https://alternative.me/crypto/fear-and-greed-index/',
        }]
    except Exception as exc:
        log.warning('Fear & Greed fetch failed: %s', exc)
        return []


def fetch_and_store_crypto_news(db_path: str, _unused_token: str = ''):
    """Fetch crypto news from free RSS feeds + Fear & Greed Index. No API key required."""
    from bot.crypto.db import upsert_crypto_news

    items  = _fetch_fear_and_greed()
    items += _fetch_rss()

    if items:
        upsert_crypto_news(db_path, items)
        log.info('Stored %d crypto news items', len(items))
