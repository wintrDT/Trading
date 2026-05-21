# tests/futures/test_strategy.py
import pytest
from bot.futures.strategy import (
    VWAPState, calc_vwap, ORBState, check_vwap_signal, check_orb_signal,
    check_exhaustion_fade,
)


def _bars(n=20, hi=100.0, lo=99.0, vol=1000.0):
    """n identical prior 1-min bars."""
    return [{'h': hi, 'l': lo, 'c': (hi + lo) / 2, 'v': vol} for _ in range(n)]


def test_exhaustion_fade_short_new_high_low_volume():
    # New high (101 > prior 100) on LOW volume (500 < 0.7*1000), extended above VWAP, overbought
    bars = _bars() + [{'h': 101.0, 'l': 100.0, 'c': 100.5, 'v': 500.0}]
    assert check_exhaustion_fade(bars, dev_pct=0.15, rsi=70) == 'short'


def test_exhaustion_fade_no_fade_on_high_volume_breakout():
    # New high but HIGH volume (1500) = real breakout — do NOT fade
    bars = _bars() + [{'h': 101.0, 'l': 100.0, 'c': 100.5, 'v': 1500.0}]
    assert check_exhaustion_fade(bars, dev_pct=0.15, rsi=70) is None


def test_exhaustion_fade_long_new_low_low_volume():
    # New low (98 < prior 99) on LOW volume, extended below VWAP, oversold
    bars = _bars() + [{'h': 99.0, 'l': 98.0, 'c': 98.5, 'v': 500.0}]
    assert check_exhaustion_fade(bars, dev_pct=-0.15, rsi=30) == 'long'


def test_exhaustion_fade_none_when_not_new_extreme():
    # Inside the prior range, low volume — nothing to fade
    bars = _bars() + [{'h': 99.8, 'l': 99.2, 'c': 99.5, 'v': 500.0}]
    assert check_exhaustion_fade(bars, dev_pct=0.15, rsi=70) is None


def test_exhaustion_fade_none_when_not_extended_from_vwap():
    # New high, low volume, but price barely off VWAP (dev below min) — skip
    bars = _bars() + [{'h': 101.0, 'l': 100.0, 'c': 100.5, 'v': 500.0}]
    assert check_exhaustion_fade(bars, dev_pct=0.02, rsi=70) is None


def test_exhaustion_fade_none_insufficient_bars():
    assert check_exhaustion_fade(_bars(n=5), dev_pct=0.15, rsi=70) is None

def test_vwap_calculation():
    state = VWAPState()
    state.add_bar(price=100.0, volume=1000)
    state.add_bar(price=102.0, volume=2000)
    vwap = calc_vwap(state)
    # (100*1000 + 102*2000) / 3000 = 101.333...
    assert abs(vwap - 101.333) < 0.001

def test_vwap_signal_long():
    signal = check_vwap_signal(current_price=99.79, vwap=100.0, deviation_pct=0.15)
    assert signal == 'long'

def test_vwap_signal_short():
    signal = check_vwap_signal(current_price=100.21, vwap=100.0, deviation_pct=0.15)
    assert signal == 'short'

def test_vwap_no_signal():
    signal = check_vwap_signal(current_price=100.05, vwap=100.0, deviation_pct=0.15)
    assert signal is None

def test_orb_not_ready_before_end():
    state = ORBState()
    state.update(price=5000.0, ts_minute=9*60+30)
    assert not state.is_ready(orb_end_minute=10*60)

def test_orb_breakout_long():
    state = ORBState()
    state._high  = 5002.0
    state._low   = 5000.0
    state._ready = True
    signal = check_orb_signal(current_price=5003.0, orb_state=state,
                               orb_end_minute=10*60, min_range_ticks=4, tick=0.25)
    assert signal == 'long'

def test_orb_breakout_short():
    state = ORBState()
    state._high  = 5002.0
    state._low   = 5000.0
    state._ready = True
    signal = check_orb_signal(current_price=4999.0, orb_state=state,
                               orb_end_minute=10*60, min_range_ticks=4, tick=0.25)
    assert signal == 'short'
