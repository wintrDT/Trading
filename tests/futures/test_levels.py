# tests/futures/test_levels.py
from bot.futures.levels import round_levels, session_levels


def test_round_levels_es_and_nq():
    # base (nearest step) +/- n steps: 7413 -> base 7425
    assert round_levels(7413.0, 'ES', n=1) == [7400.0, 7425.0, 7450.0]
    # 29240 -> base 29200
    assert round_levels(29240.0, 'NQ', n=1) == [29100.0, 29200.0, 29300.0]


def test_session_levels_prior_day_and_overnight():
    # May = EDT (UTC-4): RTH 09:30-16:00 ET = 13:30-20:00 UTC
    bars = [
        # prior-day RTH (2026-05-20)
        {'t': '2026-05-20T13:30:00Z', 'h': 95, 'l': 90, 'c': 92},   # 09:30 ET
        {'t': '2026-05-20T19:00:00Z', 'h': 100, 'l': 94, 'c': 99},  # 15:00 ET -> marks session complete
        {'t': '2026-05-20T19:59:00Z', 'h': 98, 'l': 95, 'c': 96},   # 15:59 ET -> last -> close 96
        # overnight into 2026-05-21
        {'t': '2026-05-21T06:00:00Z', 'h': 97, 'l': 91, 'c': 93},   # 02:00 ET -> morning 05-21
    ]
    lv = session_levels(bars)
    assert lv['prior_day_high'] == 100
    assert lv['prior_day_low'] == 90
    assert lv['prior_day_close'] == 96
    assert lv['overnight_high'] == 97
    assert lv['overnight_low'] == 91


def test_session_levels_incomplete_rth_is_not_prior_day():
    # An RTH session with no bar at/after 15:00 ET is not "complete" -> no prior day
    bars = [{'t': '2026-05-20T14:00:00Z', 'h': 50, 'l': 40, 'c': 45}]  # 10:00 ET only
    lv = session_levels(bars)
    assert lv['prior_day_high'] is None


def test_session_levels_empty():
    lv = session_levels([])
    assert all(v is None for v in lv.values())
