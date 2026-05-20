# bot/futures/price_feed.py
import logging
import os
from datetime import datetime
import httpx
import pytz
import yfinance as yf

_ET = pytz.timezone('America/New_York')

log = logging.getLogger(__name__)

# ETF proxies — real-time via Finnhub free tier
# Scale factors approximate ES/NQ/GC contract price levels
_ETF_MAP = {
    'ES': ('SPY', 10.0),    # SPY × 10 ≈ ES (S&P 500 futures)
    'NQ': ('QQQ', 40.0),    # QQQ × 40 ≈ NQ (Nasdaq-100 futures)
    'GC': ('GLD', 10.49),   # GLD × 10.49 ≈ GC — GLD holds 0.0953 oz/share (1/0.0953 = 10.49)
}

# Yahoo Finance fallback symbols
_YF_MAP = {
    'ES': 'ES=F',
    'NQ': 'NQ=F',
    'GC': 'GC=F',
}


def _get_finnhub_key() -> str:
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(__file__), '..', 'data', 'futures.db')
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT value FROM futures_settings WHERE key='finnhub_api_key'")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else ''
    except Exception:
        return ''


def _is_etf_market_hours() -> bool:
    """ETF prices are live only during NYSE regular session (9:30 AM – 4:00 PM ET)."""
    from datetime import time as _Time
    now = datetime.now(_ET).time()
    return _Time(9, 30) <= now < _Time(16, 0)


def get_prices(symbols: list, tradovate_client=None) -> dict:
    # Tastytrade DXFeed — actual futures contracts, real-time 23 hrs/day
    if tradovate_client is not None:
        try:
            prices = tradovate_client.get_futures_prices(symbols)
            if prices:
                log.debug('Prices via Tastytrade DXFeed: %s', prices)
                return prices
            log.warning('Tastytrade returned no prices — skipping scan')
        except Exception as e:
            log.warning('Tastytrade futures price fetch failed: %s — skipping scan', e)
    else:
        log.warning('No Tastytrade client — skipping scan')
    return {}


def get_price_history(symbol: str, bars: int = 30) -> list:
    """Fetch last N 1-minute close prices for warmup — uses Yahoo Finance (history not on Finnhub free)."""
    ticker = _YF_MAP.get(symbol)
    if not ticker:
        return []
    try:
        hist = yf.Ticker(ticker).history(period='1d', interval='1m')
        if hist.empty:
            return []
        closes = [float(p) for p in hist['Close'].tail(bars).tolist()]
        # Scale to match Finnhub proxy levels
        _, scale = _ETF_MAP.get(symbol, (None, 1.0))
        return closes  # history already in contract-level prices
    except Exception as e:
        log.warning('price_feed: history failed for %s: %s', symbol, e)
        return []
