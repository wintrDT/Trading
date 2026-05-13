# tests/futures/test_tradovate_client.py
import pytest
from unittest.mock import patch, MagicMock
from bot.futures.tradovate_client import TradovateClient


def test_auth_sets_access_token():
    client = TradovateClient('user', 'pass', 'cid', 'sec', demo=True)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'accessToken': 'tok123',
        'mdAccessToken': 'md456',
        'expirationTime': '2099-01-01T00:00:00Z',
    }
    mock_response.raise_for_status = MagicMock()
    with patch('httpx.post', return_value=mock_response), \
         patch.object(client, '_fetch_account', return_value=None):
        client.connect()
    assert client.access_token == 'tok123'
    assert client.md_access_token == 'md456'


def test_place_order_sends_correct_payload():
    client = TradovateClient('user', 'pass', 'cid', 'sec', demo=True)
    client.access_token = 'tok123'
    client._account_id   = 12345
    client._account_spec = 'user/12345'
    with patch.object(client, '_post', return_value={'orderId': 99, 'orderStatus': 'Working'}) as mock_post:
        client.place_order('ES', 'Buy', 1, order_type='Market')
    call_json = mock_post.call_args[1]['json']
    assert call_json['symbol'] == 'ES'
    assert call_json['action'] == 'Buy'
