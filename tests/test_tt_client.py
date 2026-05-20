from unittest.mock import MagicMock, AsyncMock, patch
from bot.tt_client import TastytradeClient


@patch('bot.tt_client.Account')
@patch('bot.tt_client.Session')
def test_connect_sets_session_and_account(mock_session_cls, mock_account_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    mock_account = MagicMock()
    mock_account.account_number = 'ACC123'
    mock_account_cls.get = AsyncMock(return_value=[mock_account])

    client = TastytradeClient('secret', 'refresh', 'ACC123')
    client.connect()

    assert client.session is mock_session
    assert client.account is mock_account


@patch('bot.tt_client.Account')
@patch('bot.tt_client.Session')
@patch('bot.tt_client.get_market_metrics', new_callable=AsyncMock)
def test_get_iv_rank_converts_to_0_100(mock_metrics, mock_session_cls, mock_account_cls):
    mock_session_cls.return_value = MagicMock()
    mock_account = MagicMock()
    mock_account.account_number = 'ACC123'
    mock_account_cls.get = AsyncMock(return_value=[mock_account])

    mock_metric = MagicMock()
    mock_metric.implied_volatility_index_rank = 0.42
    mock_metrics.return_value = [mock_metric]

    client = TastytradeClient('secret', 'refresh', 'ACC123')
    client.connect()
    iv_rank = client.get_iv_rank('SPY')

    assert iv_rank == 42.0


@patch('bot.tt_client.Account')
@patch('bot.tt_client.Session')
@patch('bot.tt_client.get_market_metrics', new_callable=AsyncMock)
def test_get_iv_rank_returns_none_when_empty(mock_metrics, mock_session_cls, mock_account_cls):
    mock_session_cls.return_value = MagicMock()
    mock_account_cls.get = AsyncMock(return_value=[MagicMock()])
    mock_metrics.return_value = []

    client = TastytradeClient('secret', 'refresh', 'ACC123')
    client.connect()

    assert client.get_iv_rank('SPY') is None
