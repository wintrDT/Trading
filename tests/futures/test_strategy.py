# tests/futures/test_strategy.py
import pytest
from bot.futures.strategy import VWAPState, calc_vwap, ORBState, check_vwap_signal, check_orb_signal

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
