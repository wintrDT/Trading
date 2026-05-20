from unittest.mock import MagicMock
from bot.manager import should_close, calc_pnl_pct, _get_spread_mark, _build_close_legs


def test_profit_target_hit():
    # entry=2.00, mark=1.00 → 50% profit
    assert should_close(entry_credit=2.0, current_mark=1.0) == 'profit_target'


def test_stop_loss_hit():
    # entry=2.00, mark=6.00 → 200% loss
    assert should_close(entry_credit=2.0, current_mark=6.0) == 'stop_loss'


def test_dte_expire():
    assert should_close(entry_credit=2.0, current_mark=1.8, dte=5) == 'dte_expire'


def test_no_close_midway():
    assert should_close(entry_credit=2.0, current_mark=1.5, dte=20) is None


def test_calc_pnl_pct_profit():
    assert calc_pnl_pct(entry_credit=2.0, current_mark=1.0) == 50.0


def test_calc_pnl_pct_loss():
    assert calc_pnl_pct(entry_credit=2.0, current_mark=6.0) == -200.0


def test_profit_beats_dte():
    # both profit target and DTE triggered — profit_target is checked first
    assert should_close(entry_credit=2.0, current_mark=1.0, dte=5) == 'profit_target'


def test_get_spread_mark_bps():
    pos_map = {
        'SPY   260619P00520000': 3.0,  # short put mark
        'SPY   260619P00515000': 1.0,  # long put mark
    }
    trade = {
        'underlying': 'SPY',
        'expiration': '2026-06-19',
        'short_put_strike': 520.0,
        'long_put_strike': 515.0,
        'strategy': 'bull_put_spread',
    }
    mark = _get_spread_mark(pos_map, trade)
    assert mark == 2.0  # costs 3.0 to buy back short, receive 1.0 selling long


def test_get_spread_mark_ic():
    pos_map = {
        'SPY   260619P00520000': 3.0,
        'SPY   260619P00515000': 1.0,
        'SPY   260619C00560000': 2.5,
        'SPY   260619C00565000': 0.5,
    }
    trade = {
        'underlying': 'SPY',
        'expiration': '2026-06-19',
        'short_put_strike': 520.0,
        'long_put_strike': 515.0,
        'short_call_strike': 560.0,
        'long_call_strike': 565.0,
        'strategy': 'iron_condor',
    }
    mark = _get_spread_mark(pos_map, trade)
    assert mark == 4.0  # (3.0 - 1.0) + (2.5 - 0.5) = 2.0 + 2.0


def test_build_close_legs_bps():
    trade = {
        'underlying': 'SPY',
        'expiration': '2026-06-19',
        'short_put_strike': 520.0,
        'long_put_strike': 515.0,
        'strategy': 'bull_put_spread',
        'contracts': 2,
        'short_call_strike': None,
        'long_call_strike': None,
    }
    legs = _build_close_legs(trade)
    assert len(legs) == 2
    buy_close = next(l for l in legs if l['action'] == 'BUY_TO_CLOSE')
    sell_close = next(l for l in legs if l['action'] == 'SELL_TO_CLOSE')
    assert 'P00520000' in buy_close['symbol']   # buy back the short
    assert 'P00515000' in sell_close['symbol']  # sell the long


def test_build_close_legs_ic():
    trade = {
        'underlying': 'SPY',
        'expiration': '2026-06-19',
        'short_put_strike': 520.0,
        'long_put_strike': 515.0,
        'short_call_strike': 560.0,
        'long_call_strike': 565.0,
        'strategy': 'iron_condor',
        'contracts': 1,
    }
    legs = _build_close_legs(trade)
    assert len(legs) == 4
    buy_legs = [l for l in legs if l['action'] == 'BUY_TO_CLOSE']
    sell_legs = [l for l in legs if l['action'] == 'SELL_TO_CLOSE']
    assert len(buy_legs) == 2
    assert len(sell_legs) == 2
