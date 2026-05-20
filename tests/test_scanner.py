from datetime import date
from unittest.mock import MagicMock
from bot.scanner import (
    calc_dte, passes_iv_rank, passes_delta, passes_credit_ratio,
    build_option_symbol, find_bull_put_spread, find_iron_condor,
)


def test_calc_dte():
    assert calc_dte(date(2026, 6, 6), date(2026, 5, 11)) == 26


def test_passes_iv_rank_above():
    assert passes_iv_rank(45.0) is True


def test_passes_iv_rank_below():
    assert passes_iv_rank(25.0) is False


def test_passes_iv_rank_at_threshold():
    assert passes_iv_rank(30.0) is True


def test_passes_delta_within():
    assert passes_delta(0.25) is True


def test_passes_delta_too_high():
    assert passes_delta(0.35) is False


def test_passes_delta_at_limit():
    assert passes_delta(0.30) is True


def test_passes_credit_ratio_ok():
    # 1.80 / 5.0 = 0.36 > 1/3
    assert passes_credit_ratio(credit=1.80, width=5.0) is True


def test_passes_credit_ratio_too_low():
    # 1.50 / 5.0 = 0.30 < 1/3
    assert passes_credit_ratio(credit=1.50, width=5.0) is False


def test_build_option_symbol_put():
    sym = build_option_symbol('SPY', date(2026, 6, 19), 'P', 520.0)
    assert sym == 'SPY   260619P00520000'


def test_build_option_symbol_call():
    sym = build_option_symbol('SPY', date(2026, 6, 19), 'C', 540.0)
    assert sym == 'SPY   260619C00540000'


def _make_option(strike, delta, bid, ask, opt_type='P'):
    o = MagicMock()
    o.strike_price = strike
    o.delta = delta
    o.bid = bid
    o.ask = ask
    o.option_type = opt_type
    return o


def test_find_bull_put_spread_returns_setup():
    opts = [
        _make_option(520.0, -0.25, 2.00, 2.20),   # short candidate
        _make_option(515.0, -0.15, 0.25, 0.35),   # long candidate
        _make_option(510.0, -0.08, 0.10, 0.15),
    ]
    result = find_bull_put_spread(
        'SPY', date(2026, 6, 6), opts, iv_rank=45.0, today=date(2026, 5, 11)
    )
    assert result is not None
    assert result['strategy'] == 'bull_put_spread'
    assert result['short_put_strike'] == 520.0
    assert result['long_put_strike'] == 515.0
    # credit = mid(520) - mid(515) = 2.10 - 0.30 = 1.80
    assert result['credit'] == 1.80


def test_find_bull_put_spread_skips_low_iv():
    opts = [_make_option(520.0, -0.25, 2.00, 2.20), _make_option(515.0, -0.15, 0.25, 0.35)]
    result = find_bull_put_spread(
        'SPY', date(2026, 6, 6), opts, iv_rank=20.0, today=date(2026, 5, 11)
    )
    assert result is None


def test_find_iron_condor_returns_setup():
    puts = [
        _make_option(520.0, -0.25, 2.00, 2.20, 'P'),
        _make_option(515.0, -0.15, 0.25, 0.35, 'P'),
    ]
    calls = [
        _make_option(560.0, 0.22, 1.80, 2.00, 'C'),
        _make_option(565.0, 0.12, 0.20, 0.30, 'C'),
    ]
    result = find_iron_condor(
        'SPY', date(2026, 6, 6), puts + calls, iv_rank=45.0, today=date(2026, 5, 11)
    )
    assert result is not None
    assert result['strategy'] == 'iron_condor'
    assert result['short_put_strike'] == 520.0
    assert result['short_call_strike'] == 560.0


def test_find_iron_condor_skips_low_iv():
    puts = [_make_option(520.0, -0.25, 2.00, 2.20, 'P'), _make_option(515.0, -0.15, 0.25, 0.35, 'P')]
    calls = [_make_option(560.0, 0.22, 1.80, 2.00, 'C'), _make_option(565.0, 0.12, 0.20, 0.30, 'C')]
    result = find_iron_condor('SPY', date(2026, 6, 6), puts + calls, iv_rank=20.0, today=date(2026, 5, 11))
    assert result is None


def test_find_iron_condor_returns_none_when_no_qualifying_calls():
    puts = [_make_option(520.0, -0.25, 2.00, 2.20, 'P'), _make_option(515.0, -0.15, 0.25, 0.35, 'P')]
    # calls all have delta > 0.30, so none pass the delta filter
    calls = [_make_option(560.0, 0.45, 1.80, 2.00, 'C'), _make_option(565.0, 0.40, 0.20, 0.30, 'C')]
    result = find_iron_condor('SPY', date(2026, 6, 6), puts + calls, iv_rank=45.0, today=date(2026, 5, 11))
    assert result is None
