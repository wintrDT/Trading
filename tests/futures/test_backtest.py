# tests/futures/test_backtest.py
from bot.futures.backtest import _session_key, _stops_for, _stats, _Reversion, run_backtest


def test_session_key_anchors_at_6pm_et():
    # 17:59 ET and 18:01 ET are different sessions; 18:01 belongs to the next day
    before = _session_key('2026-05-20T21:59:00+00:00')  # 17:59 ET
    after  = _session_key('2026-05-20T22:01:00+00:00')  # 18:01 ET
    assert before != after


def test_stops_for_long_and_short_direction():
    s_long, t_long = _stops_for('ES', 'long', 5000.0, atr=None, tick=0.25)
    assert s_long < 5000.0 < t_long          # long: stop below, target above
    s_short, t_short = _stops_for('ES', 'short', 5000.0, atr=None, tick=0.25)
    assert t_short < 5000.0 < s_short        # short: target below, stop above


def test_stats_aggregation():
    trades = [{'pnl': 100.0}, {'pnl': -50.0}, {'pnl': 200.0}]
    s = _stats(trades)
    assert s['n'] == 3
    assert s['pnl'] == 250.0
    assert s['win_rate'] == round(2 / 3 * 100, 1)
    assert s['expectancy'] == round(250.0 / 3, 2)


def test_stats_empty():
    s = _stats([])
    assert s['n'] == 0 and s['pnl'] == 0.0


def test_reversion_fires_long_after_retrace():
    r = _Reversion(retrace=0.25, retrace_cap_pct=0.10)
    # price stretches below VWAP (long setup), then retraces back up to confirm
    assert r.check(-0.06, 0.05, 0.05) is None   # arm long at peak -0.06
    assert r.check(-0.08, 0.05, 0.05) is None   # extends — update peak, wait
    out = r.check(-0.04, 0.05, 0.05)            # retraced 0.04 from -0.08 (>0.10? no -> uses min)
    # retrace trigger = min(0.25*0.08, 0.10) = 0.02; retraced = -0.04-(-0.08)=0.04 >= 0.02 -> long
    assert out == 'long'


def test_run_backtest_returns_structure_and_no_crash():
    # synthetic flat bars — should produce 0 trades but a valid stats dict
    bars = [{'t': f'2026-05-20T{10 + i // 60:02d}:{i % 60:02d}:00+00:00',
             'o': 5000.0, 'h': 5000.5, 'l': 4999.5, 'c': 5000.0, 'v': 1000}
            for i in range(120)]
    res = run_backtest(bars, 'ES')
    assert 'trades' in res and 'stats' in res
    assert res['stats']['n'] == 0
