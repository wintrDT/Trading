# tests/futures/test_user_stream.py
from bot.futures.topstep_client import TopstepXUserStream, TopstepXMarketStream


def test_cvd_classifies_aggressor_by_bbo():
    ms = TopstepXMarketStream(get_token=lambda: 't')
    ms._bbo['C1'] = (100.0, 100.25)          # bid / ask
    ms._on_trade(['C1', [{'price': 100.25, 'volume': 5, 'type': 0}]])   # at ask -> buy +5
    ms._on_trade(['C1', [{'price': 100.00, 'volume': 3, 'type': 1}]])   # at bid -> sell -3
    assert ms.cvd('C1', window_sec=60) == 2.0


def test_cvd_falls_back_to_type_without_quote():
    ms = TopstepXMarketStream(get_token=lambda: 't')   # no bbo
    ms._on_trade(['C2', [{'price': 50.0, 'volume': 4, 'type': 0}]])     # type 0 -> buy +4
    ms._on_trade(['C2', [{'price': 50.0, 'volume': 1, 'type': 1}]])     # type 1 -> sell -1
    assert ms.cvd('C2', window_sec=60) == 3.0


def test_cvd_zero_when_no_trades():
    ms = TopstepXMarketStream(get_token=lambda: 't')
    assert ms.cvd('NONE') == 0.0


def _stream():
    return TopstepXUserStream(get_token=lambda: 'tok', account_id=123)


def test_payload_handles_shapes():
    s = _stream()
    assert s._payload([123, {'balance': 5}]) == {'balance': 5}     # [accountId, data]
    assert s._payload([{'balance': 5}]) == {'balance': 5}          # [data]
    assert s._payload({'balance': 5}) == {'balance': 5}            # data
    assert s._payload({'action': 1, 'data': {'balance': 5}}) == {'balance': 5}  # envelope
    assert s._payload('nope') is None


def test_on_account_updates_balance_and_cantrade():
    s = _stream()
    s._on_account([123, {'balance': 47999.5, 'canTrade': False}])
    acc = s.get_account()
    assert acc['balance'] == 47999.5
    assert acc['canTrade'] is False


def test_on_position_add_and_flat():
    s = _stream()
    s._on_position([123, {'contractId': 'CON.F.US.ENQ.M26', 'size': 2, 'type': 1, 'averagePrice': 29250.0}])
    p = s.get_position('CON.F.US.ENQ.M26')
    assert p['size'] == 2 and p['side'] == 'long' and p['avgPrice'] == 29250.0
    # size 0 removes the position
    s._on_position([123, {'contractId': 'CON.F.US.ENQ.M26', 'size': 0}])
    assert s.get_position('CON.F.US.ENQ.M26') is None


def test_on_position_short_side():
    s = _stream()
    s._on_position([123, {'contractId': 'CON.F.US.EP.M26', 'size': 1, 'type': 2, 'averagePrice': 7400.0}])
    assert s.get_position('CON.F.US.EP.M26')['side'] == 'short'


def test_on_trade_records_fill():
    s = _stream()
    s._on_trade([123, {'contractId': 'CON.F.US.ENQ.M26', 'price': 29451.25, 'size': 1,
                       'side': 0, 'profitAndLoss': -125.0, 'fees': 1.9, 'commissions': 0.5,
                       'creationTimestamp': '2026-05-21T18:33:16Z'}])
    fills = s.recent_fills()
    assert len(fills) == 1
    f = fills[0]
    assert f['side'] == 'buy' and f['price'] == 29451.25 and f['pnl'] == -125.0
    assert f['fees'] == 2.4   # 1.9 + 0.5
