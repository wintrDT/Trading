# bot/crypto/price_feed.py
import logging
import httpx

log = logging.getLogger(__name__)

_COINBASE_BASE = 'https://api.exchange.coinbase.com'

# Internal symbol → Coinbase product ID
_PRODUCT_MAP = {
    'BTCUSDT':  'BTC-USD',
    'ETHUSDT':  'ETH-USD',
    'SOLUSDT':  'SOL-USD',
    'DOGEUSDT': 'DOGE-USD',
}


def get_prices(symbols: list) -> dict:
    prices = {}
    for sym in symbols:
        product = _PRODUCT_MAP.get(sym)
        if not product:
            continue
        try:
            resp = httpx.get(f'{_COINBASE_BASE}/products/{product}/ticker', timeout=10)
            resp.raise_for_status()
            prices[sym] = float(resp.json()['price'])
        except Exception as e:
            log.warning('crypto price_feed: failed %s: %s', sym, e)
    return prices


def get_latest_candles(symbols: list) -> dict:
    """Fetch the most recent 1-min candle per symbol — returns price AND real volume.
    Falls back to ticker price (volume=1) if candle endpoint fails."""
    result = {}
    for sym in symbols:
        product = _PRODUCT_MAP.get(sym)
        if not product:
            continue
        try:
            resp = httpx.get(
                f'{_COINBASE_BASE}/products/{product}/candles',
                params={'granularity': 60},
                timeout=10,
            )
            resp.raise_for_status()
            candles = resp.json()
            if candles:
                c = candles[0]  # [timestamp, low, high, open, close, volume]
                result[sym] = {'price': float(c[4]), 'volume': float(c[5])}
                continue
        except Exception as e:
            log.warning('candle fetch failed %s, trying ticker: %s', sym, e)
        # Fallback to ticker
        try:
            resp = httpx.get(f'{_COINBASE_BASE}/products/{product}/ticker', timeout=10)
            resp.raise_for_status()
            result[sym] = {'price': float(resp.json()['price']), 'volume': 1.0}
        except Exception as e:
            log.warning('ticker fallback also failed %s: %s', sym, e)
    return result


def get_5min_trends(symbols: list) -> dict:
    """Returns 'long'/'short'/None per symbol based on price vs 10-bar 5-min SMA."""
    result = {}
    for sym in symbols:
        product = _PRODUCT_MAP.get(sym)
        if not product:
            result[sym] = None
            continue
        try:
            resp = httpx.get(
                f'{_COINBASE_BASE}/products/{product}/candles',
                params={'granularity': 300},
                timeout=10,
            )
            resp.raise_for_status()
            candles = resp.json()
            closes  = [float(c[4]) for c in candles[:10]]
            if len(closes) < 10:
                result[sym] = None
                continue
            sma     = sum(closes) / 10
            current = closes[0]
            result[sym] = 'long' if current > sma else ('short' if current < sma else None)
        except Exception as e:
            log.warning('5min trend failed %s: %s', sym, e)
            result[sym] = None
    return result


def get_price_history(symbol: str, bars: int = 30) -> list:
    """Fetch last N 1-minute close prices for warmup from Coinbase candles."""
    product = _PRODUCT_MAP.get(symbol)
    if not product:
        return []
    try:
        resp = httpx.get(
            f'{_COINBASE_BASE}/products/{product}/candles',
            params={'granularity': 60},
            timeout=10,
        )
        resp.raise_for_status()
        # Each candle: [timestamp, low, high, open, close, volume] — newest first
        candles = resp.json()
        closes = [float(c[4]) for c in candles[:bars]]
        return list(reversed(closes))  # oldest → newest for proper warmup order
    except Exception as e:
        log.warning('crypto price_feed: history failed for %s: %s', symbol, e)
        return []
