# bot/futures/strategy.py
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


def check_orb_signal(current_price: float, orb_state: ORBState,
                     orb_end_minute: int, min_range_ticks: int, tick: float) -> str | None:
    if not orb_state._ready:
        return None
    orb_range_ticks = round((orb_state.high - orb_state.low) / tick)
    if orb_range_ticks < min_range_ticks:
        return None
    if current_price > orb_state.high:
        return 'long'
    if current_price < orb_state.low:
        return 'short'
    return None
