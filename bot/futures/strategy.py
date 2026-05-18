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
