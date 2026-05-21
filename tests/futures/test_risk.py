# tests/futures/test_risk.py
import pytest
from bot.futures.risk import (
    calc_stop_price, calc_target_price, calc_pnl, is_daily_loss_limit_hit,
    is_news_blackout, should_exit, trailing_floor, drawdown_halt_decision,
)


def test_trailing_floor_locks_at_start():
    assert trailing_floor(peak=50000, trailing=2000, start=50000) == 48000
    assert trailing_floor(peak=51000, trailing=2000, start=50000) == 49000
    assert trailing_floor(peak=52000, trailing=2000, start=50000) == 50000   # locked
    assert trailing_floor(peak=53000, trailing=2000, start=50000) == 50000   # stays locked


def test_drawdown_halt_when_locked():
    halt, reason = drawdown_halt_decision(49000, 50000, can_trade=False,
                                          start=50000, trailing=2000, buffer=400)
    assert halt is True and 'locked' in reason


def test_drawdown_halt_within_buffer():
    # peak 50k -> floor 48000; halt at <= 48400 (400 buffer)
    halt, _ = drawdown_halt_decision(48350, 50000, True, 50000, 2000, 400)
    assert halt is True
    halt2, _ = drawdown_halt_decision(48600, 50000, True, 50000, 2000, 400)
    assert halt2 is False        # still $200 above the halt line


def test_drawdown_no_halt_with_room():
    halt, reason = drawdown_halt_decision(49693, 50000, True, 50000, 2000, 400)
    assert halt is False and reason is None


def test_drawdown_halt_on_profit_target():
    halt, reason = drawdown_halt_decision(53050, 53050, True, 50000, 2000, 400, profit_target=3000)
    assert halt is True and 'profit target' in reason

def test_stop_price_long():
    stop = calc_stop_price('long', entry=5000.25, stop_ticks=8, tick=0.25)
    assert stop == 4998.25

def test_stop_price_short():
    stop = calc_stop_price('short', entry=5000.25, stop_ticks=8, tick=0.25)
    assert stop == 5002.25

def test_target_price_long():
    target = calc_target_price('long', entry=5000.25, target_ticks=16, tick=0.25)
    assert target == 5004.25

def test_target_price_short():
    target = calc_target_price('short', entry=5000.25, target_ticks=16, tick=0.25)
    assert target == 4996.25

def test_pnl_long_win():
    pnl = calc_pnl('long', entry=5000.0, close=5004.0, contracts=1, point_value=50.0)
    assert pnl == 200.0

def test_pnl_short_loss():
    pnl = calc_pnl('short', entry=5000.0, close=5004.0, contracts=1, point_value=50.0)
    assert pnl == -200.0

def test_daily_loss_limit_hit():
    assert is_daily_loss_limit_hit(realized=-600.0, limit=500.0) is True
    assert is_daily_loss_limit_hit(realized=-400.0, limit=500.0) is False

def test_news_blackout():
    assert is_news_blackout('2026-01-15T08:30:00', blackout_minutes=5,
                             news_times=['2026-01-15T08:30:00']) is True
    assert is_news_blackout('2026-01-15T08:20:00', blackout_minutes=5,
                             news_times=['2026-01-15T08:30:00']) is False

def test_should_exit_stop_loss_long():
    assert should_exit('long', current_price=4997.0, stop_price=4998.0, target_price=5004.0) == 'stop_loss'

def test_should_exit_profit_target_long():
    assert should_exit('long', current_price=5005.0, stop_price=4998.0, target_price=5004.0) == 'profit_target'

def test_should_exit_none():
    assert should_exit('long', current_price=5001.0, stop_price=4998.0, target_price=5004.0) is None
