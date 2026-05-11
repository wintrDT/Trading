import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'options.db')

UNDERLYINGS = ['SPY', 'QQQ', 'IWM']

ENTRY_RULES = {
    'max_delta': 0.30,
    'min_iv_rank': 30,
    'min_dte': 21,
    'max_dte': 45,
    'min_credit_to_width_ratio': 1 / 3,
}

EXIT_RULES = {
    'profit_target_pct': 50,
    'stop_loss_pct': 200,
    'dte_close': 7,
}

POSITION_SIZING = {
    'max_pct_per_trade': 0.05,
}

MARKET_OPEN = '09:45'
MARKET_CLOSE = '15:45'
TIMEZONE = 'America/New_York'

# Required environment variables:
#   TT_SECRET  — Tastytrade provider secret (OAuth client secret)
#   TT_REFRESH — Tastytrade refresh token
#   TT_ACCOUNT — Tastytrade account number (e.g. 5WT12345)
# Obtain these from the Tastytrade developer portal or your broker dashboard.
TASTYTRADE_PROVIDER_SECRET = os.environ['TT_SECRET']
TASTYTRADE_REFRESH_TOKEN = os.environ['TT_REFRESH']
TASTYTRADE_ACCOUNT_NUMBER = os.environ['TT_ACCOUNT']

FILL_WAIT_SECS = 120
