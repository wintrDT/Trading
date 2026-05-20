import logging
from datetime import date
from bot.calendar import straddles_high_impact
from bot.config import ENTRY_RULES, ZERO_DTE_ENTRY_RULES

log = logging.getLogger(__name__)


def calc_dte(expiration: date, today: date = None) -> int:
    if today is None:
        today = date.today()
    return (expiration - today).days


def passes_iv_rank(iv_rank: float) -> bool:
    return iv_rank >= ENTRY_RULES['min_iv_rank']


def passes_delta(delta: float) -> bool:
    return abs(delta) <= ENTRY_RULES['max_delta']


def passes_credit_ratio(credit: float, width: float) -> bool:
    if width <= 0:
        return False
    return credit / width >= ENTRY_RULES['min_credit_to_width_ratio']


def build_option_symbol(underlying: str, expiration: date, opt_type: str, strike: float) -> str:
    exp_str = expiration.strftime('%y%m%d')
    strike_int = int(round(strike * 1000))
    return f"{underlying:<6}{exp_str}{opt_type}{strike_int:08d}"


def _mid(opt) -> float:
    return (float(opt.bid) + float(opt.ask)) / 2


def find_bull_put_spread(underlying, expiration, options, iv_rank, today=None):
    if not passes_iv_rank(iv_rank):
        return None
    if today is None:
        today = date.today()
    dte = calc_dte(expiration, today)
    if not (ENTRY_RULES['min_dte'] <= dte <= ENTRY_RULES['max_dte']):
        return None

    puts = sorted(
        [o for o in options if str(o.option_type).upper() in ('P', 'PUT')],
        key=lambda o: float(o.strike_price),
        reverse=True,
    )

    for short_put in puts:
        if not passes_delta(float(short_put.delta)):
            continue
        short_strike = float(short_put.strike_price)
        short_mid = _mid(short_put)

        for long_put in puts:
            long_strike = float(long_put.strike_price)
            if long_strike >= short_strike:
                continue
            width = round(short_strike - long_strike, 2)
            if width < 1.0:
                continue
            credit = round(short_mid - _mid(long_put), 2)
            if credit <= 0 or not passes_credit_ratio(credit, width):
                continue
            return {
                'underlying': underlying,
                'strategy': 'bull_put_spread',
                'expiration': expiration.isoformat(),
                'short_put_strike': short_strike,
                'long_put_strike': long_strike,
                'short_call_strike': None,
                'long_call_strike': None,
                'credit': credit,
                'width': width,
                'delta': abs(float(short_put.delta)),
                'iv_rank': iv_rank,
                'dte': dte,
            }
    return None


def find_iron_condor(underlying, expiration, options, iv_rank, today=None):
    if not passes_iv_rank(iv_rank):
        return None
    if today is None:
        today = date.today()
    dte = calc_dte(expiration, today)
    if not (ENTRY_RULES['min_dte'] <= dte <= ENTRY_RULES['max_dte']):
        return None

    puts = sorted(
        [o for o in options if str(o.option_type).upper() in ('P', 'PUT')],
        key=lambda o: float(o.strike_price), reverse=True,
    )
    calls = sorted(
        [o for o in options if str(o.option_type).upper() in ('C', 'CALL')],
        key=lambda o: float(o.strike_price),
    )

    put_spread = None
    sp_delta = 0.0
    for sp in puts:
        if not passes_delta(float(sp.delta)):
            continue
        sp_strike = float(sp.strike_price)
        for lp in puts:
            lp_strike = float(lp.strike_price)
            if lp_strike >= sp_strike:
                continue
            width = round(sp_strike - lp_strike, 2)
            if width < 1.0:
                continue
            credit = round(_mid(sp) - _mid(lp), 2)
            if credit > 0:
                put_spread = (sp_strike, lp_strike, credit, width)
                sp_delta = abs(float(sp.delta))
                break
        if put_spread:
            break

    call_spread = None
    sc_delta = 0.0
    for sc in calls:
        if not passes_delta(abs(float(sc.delta))):
            continue
        sc_strike = float(sc.strike_price)
        for lc in calls:
            lc_strike = float(lc.strike_price)
            if lc_strike <= sc_strike:
                continue
            width = round(lc_strike - sc_strike, 2)
            if width < 1.0:
                continue
            credit = round(_mid(sc) - _mid(lc), 2)
            if credit > 0:
                call_spread = (sc_strike, lc_strike, credit, width)
                sc_delta = abs(float(sc.delta))
                break
        if call_spread:
            break

    if not put_spread or not call_spread:
        return None

    sp_strike, lp_strike, put_credit, put_width = put_spread
    sc_strike, lc_strike, call_credit, call_width = call_spread
    total_credit = round(put_credit + call_credit, 2)
    max_width = max(put_width, call_width)

    if not passes_credit_ratio(total_credit, max_width):
        return None

    return {
        'underlying': underlying,
        'strategy': 'iron_condor',
        'expiration': expiration.isoformat(),
        'short_put_strike': sp_strike,
        'long_put_strike': lp_strike,
        'short_call_strike': sc_strike,
        'long_call_strike': lc_strike,
        'credit': total_credit,
        'width': max_width,
        'delta': max(sp_delta, sc_delta),
        'iv_rank': iv_rank,
        'dte': dte,
    }


def find_0dte_iron_condor(underlying, expiration, options, today=None):
    """Find a 0DTE iron condor using tighter delta/credit rules."""
    if today is None:
        today = date.today()

    max_delta = ZERO_DTE_ENTRY_RULES['max_delta']
    min_ratio = ZERO_DTE_ENTRY_RULES['min_credit_to_width_ratio']

    puts = sorted(
        [o for o in options if str(o.option_type).upper() in ('P', 'PUT')],
        key=lambda o: float(o.strike_price), reverse=True,
    )
    calls = sorted(
        [o for o in options if str(o.option_type).upper() in ('C', 'CALL')],
        key=lambda o: float(o.strike_price),
    )

    put_spread = None
    sp_delta = 0.0
    for sp in puts:
        if abs(float(sp.delta)) > max_delta:
            continue
        sp_strike = float(sp.strike_price)
        for lp in puts:
            lp_strike = float(lp.strike_price)
            if lp_strike >= sp_strike:
                continue
            width = round(sp_strike - lp_strike, 2)
            if width < 1.0:
                continue
            credit = round(_mid(sp) - _mid(lp), 2)
            if credit > 0 and credit / width >= min_ratio:
                put_spread = (sp_strike, lp_strike, credit, width)
                sp_delta = abs(float(sp.delta))
                break
        if put_spread:
            break

    call_spread = None
    sc_delta = 0.0
    for sc in calls:
        if abs(float(sc.delta)) > max_delta:
            continue
        sc_strike = float(sc.strike_price)
        for lc in calls:
            lc_strike = float(lc.strike_price)
            if lc_strike <= sc_strike:
                continue
            width = round(lc_strike - sc_strike, 2)
            if width < 1.0:
                continue
            credit = round(_mid(sc) - _mid(lc), 2)
            if credit > 0 and credit / width >= min_ratio:
                call_spread = (sc_strike, lc_strike, credit, width)
                sc_delta = abs(float(sc.delta))
                break
        if call_spread:
            break

    if not put_spread or not call_spread:
        return None

    sp_strike, lp_strike, put_credit, put_width = put_spread
    sc_strike, lc_strike, call_credit, call_width = call_spread
    total_credit = round(put_credit + call_credit, 2)
    max_width = max(put_width, call_width)

    return {
        'underlying': underlying,
        'strategy': 'iron_condor',
        'expiration': expiration.isoformat(),
        'short_put_strike': sp_strike,
        'long_put_strike': lp_strike,
        'short_call_strike': sc_strike,
        'long_call_strike': lc_strike,
        'credit': total_credit,
        'width': max_width,
        'delta': max(sp_delta, sc_delta),
        'iv_rank': 0,
        'dte': 0,
        'trade_type': '0dte',
    }


def scan_underlying_0dte(client, underlying, today=None):
    """Return 0DTE iron condor setup for one underlying, or empty list."""
    if today is None:
        today = date.today()

    exp_map = client.get_options_with_market_data(underlying, 0, 0)
    setups = []
    for exp_date, options in exp_map.items():
        if straddles_high_impact(exp_date, today):
            log.info('0DTE: Skipping %s exp %s — high-impact event', underlying, exp_date)
            continue
        ic = find_0dte_iron_condor(underlying, exp_date, options, today)
        if ic:
            setups.append(ic)
    return setups


def scan_underlying(client, underlying, today=None):
    """Return list of qualifying setup dicts for one underlying."""
    if today is None:
        today = date.today()

    iv_rank = client.get_iv_rank(underlying)
    if iv_rank is None or not passes_iv_rank(iv_rank):
        return []

    price, sma = client.get_sma_and_price(underlying)
    uptrend = price is not None and sma is not None and price > sma
    if price is not None:
        log.info('%s price=%.2f sma20=%.2f uptrend=%s', underlying, price, sma, uptrend)

    exp_map = client.get_options_with_market_data(
        underlying,
        ENTRY_RULES['min_dte'],
        ENTRY_RULES['max_dte'],
    )
    setups = []

    for exp_date, options in exp_map.items():
        if straddles_high_impact(exp_date, today):
            log.info('Skipping %s exp %s — straddles high-impact economic event', underlying, exp_date)
            continue

        if uptrend:
            bps = find_bull_put_spread(underlying, exp_date, options, iv_rank, today)
            if bps:
                setups.append(bps)

        ic = find_iron_condor(underlying, exp_date, options, iv_rank, today)
        if ic:
            setups.append(ic)

    return setups
