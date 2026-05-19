# bot/futures/strategy.py
from collections import deque
from dataclasses import dataclass, field


@dataclass
class VWAPState:
    _cum_pv: float = field(default=0.0)
    _cum_v:  float = field(default=0.0)

    def add_bar(self, price: float, volume: float):
        self._cum_pv += price * volume
        self._cum_v  += volume

    def reset(self):
        self._cum_pv = 0.0
        self._cum_v  = 0.0


def calc_vwap(state: VWAPState) -> float | None:
    if state._cum_v == 0:
        return None
    return state._cum_pv / state._cum_v


def check_vwap_signal(current_price: float, vwap: float, deviation_pct: float) -> str | None:
    if vwap == 0:
        return None
    dev = (current_price - vwap) / vwap * 100
    if dev <= -deviation_pct:
        return 'long'
    if dev >= deviation_pct:
        return 'short'
    return None


@dataclass
class ORBState:
    _high:  float = field(default_factory=lambda: float('-inf'))
    _low:   float = field(default_factory=lambda: float('inf'))
    _ready: bool  = field(default=False)

    def update(self, price: float, ts_minute: int):
        self._high = max(self._high, price)
        self._low  = min(self._low,  price)

    def is_ready(self, orb_end_minute: int) -> bool:
        return self._ready

    def set_ready(self):
        self._ready = True

    @property
    def high(self) -> float:
        return self._high

    @property
    def low(self) -> float:
        return self._low


@dataclass
class ChannelState:
    _window: deque = field(default_factory=lambda: deque(maxlen=10))

    def update(self, price: float):
        self._window.append(price)

    def ready(self) -> bool:
        return len(self._window) >= 10


@dataclass
class SMAState:
    _window: deque = field(default_factory=lambda: deque(maxlen=20))

    def update(self, price: float):
        self._window.append(price)

    def value(self) -> float | None:
        if len(self._window) < 20:
            return None
        return sum(self._window) / len(self._window)


def check_channel_signal(current_price: float, state: ChannelState,
                          sma: float | None, min_width_pct: float = 0.01) -> str | None:
    if not state.ready():
        return None
    window = list(state._window)[:-1]
    if not window:
        return None
    hi = max(window)
    lo = min(window)

    # Skip only the tightest chop (< 0.01% range)
    mid = (hi + lo) / 2
    width_pct = (hi - lo) / mid * 100 if mid else 0
    if width_pct < min_width_pct:
        return None

    # SMA trend filter: long only above SMA, short only below SMA
    if current_price > hi:
        if sma is None or current_price > sma:
            return 'long'
    if current_price < lo:
        if sma is None or current_price < sma:
            return 'short'
    return None


@dataclass
class VolatilityState:
    """Rolling std dev of price returns — used to set adaptive VWAP threshold."""
    _prices: deque = field(default_factory=lambda: deque(maxlen=20))

    def update(self, price: float):
        self._prices.append(price)

    def threshold(self, multiplier: float = 2.0, floor: float = 0.03, ceiling: float = 0.20) -> float | None:
        if len(self._prices) < 10:
            return None
        prices = list(self._prices)
        returns = [(prices[i] - prices[i-1]) / prices[i-1] * 100 for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std_dev = variance ** 0.5
        raw = multiplier * std_dev
        return round(max(floor, min(ceiling, raw)), 4)

    def atr(self) -> float | None:
        """Average absolute price change per bar over the window. Price units (not ticks)."""
        if len(self._prices) < 5:
            return None
        prices = list(self._prices)
        moves = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        return sum(moves) / len(moves)

    # Separate longer window for "regime" comparison — atr() is the instant
    # reading (~20 bars), atr_baseline() smooths over ~60 bars for ratio checks.
    _baseline_prices: deque = field(default_factory=lambda: deque(maxlen=60))

    def update_baseline(self, price: float):
        self._baseline_prices.append(price)

    def atr_baseline(self) -> float | None:
        if len(self._baseline_prices) < 30:
            return None
        prices = list(self._baseline_prices)
        moves = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        return sum(moves) / len(moves)


# ---------------------------------------------------------------------------
# Day-type classification
# ---------------------------------------------------------------------------
# Six regimes derived from gap, ORB position, volatility ratio, and SMA trend.
# Computed once per session ~10:00 ET (after ORB completes) and cached.
DAY_TYPES = (
    'trend_day_up', 'trend_day_down',
    'gap_and_go_up', 'gap_and_go_down',
    'auction_reversal',
    'news_expansion',
    'low_vol_chop',
    'balance_day',
)


def classify_day_type(
    prev_close: float | None,
    orb_state: 'ORBState',
    vwap: float | None,
    current_price: float,
    sma: float | None,
    atr: float | None,
    atr_baseline: float | None,
    tick: float,
) -> str:
    """Classify the trading day. See DAY_TYPES for valid return values.

    Detection order matters — first matching rule wins.
    """
    # ORB not ready or unset — fall back to default
    if not orb_state._ready or orb_state.high == float('-inf') or orb_state.low == float('inf'):
        return 'balance_day'

    orb_high  = orb_state.high
    orb_low   = orb_state.low
    orb_range = orb_high - orb_low
    orb_mid   = (orb_high + orb_low) / 2.0

    # Gap from prior session close (ORB midpoint vs prev close)
    gap_pct = ((orb_mid - prev_close) / prev_close * 100.0) if prev_close else 0.0
    abs_gap = abs(gap_pct)

    # Volatility regime — checked first
    if atr is not None and atr_baseline and atr_baseline > 0:
        ratio = atr / atr_baseline
        if ratio >= 2.0:
            return 'news_expansion'
        # Low-vol chop = quiet AND tight ORB
        if ratio < 0.5 and orb_range < 4 * tick:
            return 'low_vol_chop'

    # Gap-and-go: ≥0.5% gap, price extended in gap direction past ORB extreme
    if abs_gap >= 0.5:
        if gap_pct > 0 and current_price > orb_high:
            return 'gap_and_go_up'
        if gap_pct < 0 and current_price < orb_low:
            return 'gap_and_go_down'
        # Auction reversal: notable gap that's been reversed back through ORB
        if abs_gap >= 0.3:
            if gap_pct > 0 and current_price < orb_low:
                return 'auction_reversal'
            if gap_pct < 0 and current_price > orb_high:
                return 'auction_reversal'

    # Trend day: price decisively past ORB high/low AND aligned with SMA
    if current_price > orb_high and (sma is None or current_price > sma):
        return 'trend_day_up'
    if current_price < orb_low and (sma is None or current_price < sma):
        return 'trend_day_down'

    return 'balance_day'


def day_type_blocks_direction(day_type: str | None, direction: str) -> str | None:
    """Returns a blocking reason string if the day type forbids this direction.
    Returns None when the direction is allowed.

    Trend day: only with-trend entries.
    Gap-and-go: only with-gap entries.
    Other types: allowed (regulation comes from sizing, not blocking).
    """
    if not day_type:
        return None
    if day_type == 'trend_day_up' and direction == 'short':
        return 'trend day up — no shorts'
    if day_type == 'trend_day_down' and direction == 'long':
        return 'trend day down — no longs'
    if day_type == 'gap_and_go_up' and direction == 'short':
        return 'gap-and-go up — no shorts'
    if day_type == 'gap_and_go_down' and direction == 'long':
        return 'gap-and-go down — no longs'
    return None


# ---------------------------------------------------------------------------
# Confidence scoring 0-100
# ---------------------------------------------------------------------------
# Inputs we have today (no paid Level-2 feed required):
#   - VWAP deviation magnitude vs threshold       (0-25 pts)
#   - SMA trend agreement with signal direction   (0-20 pts)
#   - Day-type favorability for this trade        (0-20 pts)
#   - RSI in confirming range                     (0-15 pts)
#   - ATR regime is normal (not extreme)          (0-10 pts)
#   - News calm (no near-event scaling triggered) (0-10 pts)
# Total possible: 100. We block entries below a configurable threshold.
#
# When paid data is added (Polygon/Databento): cumulative delta and liquidity
# sweep slots get appended here and the score expands to 130-150.
def compute_confidence(
    *,
    direction: str,                       # 'long' or 'short'
    dev_pct: float | None,                # VWAP deviation in %
    dev_threshold: float,                 # current dynamic threshold
    sma_trend: str | None,                # 'up' / 'down' / None
    day_type: str | None,                 # one of DAY_TYPES
    rsi: float | None,                    # current RSI
    atr_ratio: float | None,              # atr / atr_baseline (1.0 = normal)
    near_event: bool,                     # True if within 15 min of news
    bias_disagrees: bool,                 # True if rolling sentiment disagrees
) -> tuple[int, dict]:
    """Returns (score_0_to_100, breakdown_dict_for_display)."""
    breakdown = {}

    # 1. VWAP deviation strength (0-25): more extreme = stronger signal
    if dev_pct is not None and dev_threshold > 0:
        ratio = abs(dev_pct) / dev_threshold
        # 1x threshold = 10 pts, 2x = 20 pts, 3x+ = 25 pts (cap)
        dev_score = min(25, int(ratio * 10))
    else:
        dev_score = 0
    breakdown['dev'] = dev_score

    # 2. SMA trend agreement (0-20)
    if sma_trend == direction or (sma_trend == 'up' and direction == 'long') or (sma_trend == 'down' and direction == 'short'):
        trend_score = 20
    elif sma_trend is None:
        trend_score = 10  # unknown = neutral
    else:
        trend_score = 0
    breakdown['trend'] = trend_score

    # 3. Day-type favorability (0-20)
    favorable = {
        'long':  {'trend_day_up': 20, 'gap_and_go_up': 18, 'balance_day': 12,
                  'auction_reversal': 14, 'news_expansion': 6, 'low_vol_chop': 8,
                  'trend_day_down': 0, 'gap_and_go_down': 0},
        'short': {'trend_day_down': 20, 'gap_and_go_down': 18, 'balance_day': 12,
                  'auction_reversal': 14, 'news_expansion': 6, 'low_vol_chop': 8,
                  'trend_day_up': 0, 'gap_and_go_up': 0},
    }
    dt_score = favorable.get(direction, {}).get(day_type or 'balance_day', 10)
    breakdown['day_type'] = dt_score

    # 4. RSI in confirming range (0-15)
    # For reversion long: RSI low-to-mid = good (still oversold-ish but bouncing)
    # For reversion short: RSI mid-to-high = good
    if rsi is None:
        rsi_score = 8
    elif direction == 'long':
        # 30-55 is sweet spot for a reversion bounce; above 70 = exhausted
        if   30 <= rsi <= 55: rsi_score = 15
        elif 25 <= rsi < 30:  rsi_score = 12
        elif 55 <  rsi <= 65: rsi_score = 10
        elif 65 <  rsi <= 75: rsi_score = 5
        else:                 rsi_score = 0
    else:  # short
        if   45 <= rsi <= 70: rsi_score = 15
        elif 70 <  rsi <= 75: rsi_score = 12
        elif 35 <= rsi < 45:  rsi_score = 10
        elif 25 <= rsi < 35:  rsi_score = 5
        else:                 rsi_score = 0
    breakdown['rsi'] = rsi_score

    # 5. ATR regime is normal (0-10) — extreme volatility = lower confidence
    if atr_ratio is None:
        atr_score = 5
    elif 0.7 <= atr_ratio <= 1.5:
        atr_score = 10
    elif 0.5 <= atr_ratio < 0.7 or 1.5 < atr_ratio <= 2.0:
        atr_score = 6
    else:
        atr_score = 2
    breakdown['atr'] = atr_score

    # 6. News calm (0-10) — anything near event = penalty
    news_score = 10 if not near_event else 4
    if bias_disagrees:
        news_score = max(0, news_score - 4)
    breakdown['news'] = news_score

    total = dev_score + trend_score + dt_score + rsi_score + atr_score + news_score
    return min(100, total), breakdown


@dataclass
class RSIState:
    _prices: deque = field(default_factory=lambda: deque(maxlen=15))

    def update(self, price: float):
        self._prices.append(price)

    def value(self) -> float | None:
        if len(self._prices) < 15:
            return None
        changes = [self._prices[i] - self._prices[i - 1] for i in range(1, 15)]
        avg_gain = sum(max(c, 0.0) for c in changes) / 14
        avg_loss = sum(max(-c, 0.0) for c in changes) / 14
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))


def check_rsi_filter(rsi: float | None, direction: str) -> bool:
    """Return False to block trades when RSI indicates extreme exhaustion."""
    if rsi is None:
        return True  # not enough bars yet — allow
    if direction == 'long'  and rsi > 75:
        return False  # extremely overbought
    if direction == 'short' and rsi < 25:
        return False  # extremely oversold
    return True


def check_orb_signal(current_price: float, orb_state: ORBState,
                     orb_end_minute: int, min_range_ticks: int, tick: float) -> str | None:
    if not orb_state._ready:
        return None
    # Guard against unset ORB state (bot started after ORB period ended)
    if orb_state.high == float('-inf') or orb_state.low == float('inf'):
        return None
    orb_range_ticks = round((orb_state.high - orb_state.low) / tick)
    if orb_range_ticks < min_range_ticks:
        return None
    if current_price > orb_state.high:
        return 'long'
    if current_price < orb_state.low:
        return 'short'
    return None


@dataclass
class CandleBar:
    """A single OHLC bar — aggregated from scan-tick prices over `bar_seconds`."""
    open:  float
    high:  float
    low:   float
    close: float
    start_ts: float  # epoch seconds (bar boundary)


@dataclass
class CandleState:
    """Aggregates scan-time prices into rolling N-second OHLC bars.

    At 30s scan interval and bar_seconds=60, you get one closed bar per
    minute. last_bars(2) returns the two most recently CLOSED bars (not
    the in-progress one) — pattern detection runs on those.
    """
    bar_seconds: int = 60
    _bars: deque = field(default_factory=lambda: deque(maxlen=30))
    _current: dict = field(default_factory=dict)
    _current_start: float = 0.0

    def add_tick(self, price: float, timestamp: float):
        bar_start = (int(timestamp) // self.bar_seconds) * self.bar_seconds

        # No current bar → bootstrap
        if not self._current:
            self._current = {'open': price, 'high': price, 'low': price, 'close': price}
            self._current_start = bar_start
            return

        # Bar boundary crossed → close current, start new
        if bar_start > self._current_start:
            self._bars.append(CandleBar(
                open=self._current['open'],
                high=self._current['high'],
                low=self._current['low'],
                close=self._current['close'],
                start_ts=self._current_start,
            ))
            self._current = {'open': price, 'high': price, 'low': price, 'close': price}
            self._current_start = bar_start
        else:
            # Still in same bar → update high/low/close
            self._current['high']  = max(self._current['high'], price)
            self._current['low']   = min(self._current['low'],  price)
            self._current['close'] = price

    def last_bars(self, n: int = 2) -> list:
        """Returns up to n most recently CLOSED bars (excludes in-progress)."""
        return list(self._bars)[-n:]


def _body(b: CandleBar)   -> float: return abs(b.close - b.open)
def _upper(b: CandleBar)  -> float: return b.high - max(b.open, b.close)
def _lower(b: CandleBar)  -> float: return min(b.open, b.close) - b.low
def _range(b: CandleBar)  -> float: return b.high - b.low


def detect_doji(b: CandleBar) -> bool:
    """Body < 10% of total range — indecision."""
    rng = _range(b)
    return rng > 0 and _body(b) / rng < 0.10


def detect_hammer(b: CandleBar) -> bool:
    """Long lower wick, small body near top — bullish reversal."""
    body = _body(b)
    if body == 0:
        return False
    return _lower(b) >= 2 * body and _upper(b) <= 0.3 * body


def detect_shooting_star(b: CandleBar) -> bool:
    """Long upper wick, small body near bottom — bearish reversal."""
    body = _body(b)
    if body == 0:
        return False
    return _upper(b) >= 2 * body and _lower(b) <= 0.3 * body


def detect_bullish_engulfing(prev: CandleBar, curr: CandleBar) -> bool:
    """Green bar fully engulfs prior red bar's body."""
    return (prev.close < prev.open and          # prior bearish
            curr.close > curr.open and          # current bullish
            curr.open  <= prev.close and        # opens at/below prior close
            curr.close >= prev.open)            # closes at/above prior open


def detect_bearish_engulfing(prev: CandleBar, curr: CandleBar) -> bool:
    """Red bar fully engulfs prior green bar's body."""
    return (prev.close > prev.open and          # prior bullish
            curr.close < curr.open and          # current bearish
            curr.open  >= prev.close and        # opens at/above prior close
            curr.close <= prev.open)            # closes at/below prior open


def check_candle_confirmation(bars: list, direction: str) -> str:
    """Mode A gate. Returns one of:
      'confirm:<pattern>'  — last closed bar supports `direction`, allow entry
      'block:<reason>'     — last closed bar contradicts `direction` or is doji
      'neutral'            — no clear pattern; allow entry by default
    """
    if not bars:
        return 'neutral'
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else None

    if detect_doji(last):
        return 'block:doji'

    if direction == 'long':
        if detect_hammer(last):
            return 'confirm:hammer'
        if prev and detect_bullish_engulfing(prev, last):
            return 'confirm:bull_engulfing'
        if detect_shooting_star(last):
            return 'block:shooting_star'
        if prev and detect_bearish_engulfing(prev, last):
            return 'block:bear_engulfing'

    elif direction == 'short':
        if detect_shooting_star(last):
            return 'confirm:shooting_star'
        if prev and detect_bearish_engulfing(prev, last):
            return 'confirm:bear_engulfing'
        if detect_hammer(last):
            return 'block:hammer'
        if prev and detect_bullish_engulfing(prev, last):
            return 'block:bull_engulfing'

    return 'neutral'
