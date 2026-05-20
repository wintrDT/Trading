from datetime import date
from bot.trader import calc_contracts, build_bps_legs, build_ic_legs


def test_calc_contracts_basic():
    # 25000 * 0.05 = 1250 max risk / (5.0 * 100) = 2.5 → floor = 2
    assert calc_contracts(net_liq=25000, width=5.0) == 2


def test_calc_contracts_large_account():
    # 100000 * 0.05 = 5000 / 500 = 10
    assert calc_contracts(net_liq=100000, width=5.0) == 10


def test_calc_contracts_too_small_returns_zero():
    # 5000 * 0.05 = 250 / 500 = 0.5 → floor = 0 → skip trade
    assert calc_contracts(net_liq=5000, width=5.0) == 0


def test_build_bps_legs_has_two_legs():
    legs = build_bps_legs('SPY', date(2026, 6, 19), 520.0, 515.0, contracts=1)
    assert len(legs) == 2
    actions = {l['action'] for l in legs}
    assert actions == {'SELL_TO_OPEN', 'BUY_TO_OPEN'}


def test_build_bps_legs_sell_is_higher_strike():
    legs = build_bps_legs('SPY', date(2026, 6, 19), 520.0, 515.0, contracts=1)
    sell = next(l for l in legs if l['action'] == 'SELL_TO_OPEN')
    buy = next(l for l in legs if l['action'] == 'BUY_TO_OPEN')
    assert 'P00520000' in sell['symbol']
    assert 'P00515000' in buy['symbol']


def test_build_ic_legs_has_four_legs():
    legs = build_ic_legs(
        'SPY', date(2026, 6, 19),
        short_put=520.0, long_put=515.0,
        short_call=560.0, long_call=565.0,
        contracts=1,
    )
    assert len(legs) == 4
    sell_legs = [l for l in legs if l['action'] == 'SELL_TO_OPEN']
    buy_legs = [l for l in legs if l['action'] == 'BUY_TO_OPEN']
    assert len(sell_legs) == 2
    assert len(buy_legs) == 2
