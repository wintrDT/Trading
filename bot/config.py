import os
from dotenv import load_dotenv

load_dotenv()

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

ZERO_DTE_ENTRY_RULES = {
    'max_delta': 0.20,
    'min_credit_to_width_ratio': 1 / 4,
}

ZERO_DTE_EXIT_RULES = {
    'profit_target_pct': 50,
    'stop_loss_pct': 100,   # tighter — 0DTE can move fast
    'latest_close_time': '15:00',  # force-close by 3 PM to avoid gamma risk
}

POSITION_SIZING = {
    'max_pct_per_trade': 0.05,
}

MARKET_OPEN = '09:45'
MARKET_CLOSE = '15:30'
TIMEZONE = 'America/New_York'

VIX_MIN = 15.0
VIX_MAX = 30.0
SMA_DAYS = 20

# Required environment variables:
#   TT_SECRET  — Tastytrade provider secret (OAuth client secret)
#   TT_REFRESH — Tastytrade refresh token
#   TT_ACCOUNT — Tastytrade account number (e.g. 5WT12345)
# Obtain these from the Tastytrade developer portal or your broker dashboard.
TASTYTRADE_PROVIDER_SECRET = os.environ['TT_SECRET']
TASTYTRADE_REFRESH_TOKEN = os.environ['TT_REFRESH']
TASTYTRADE_ACCOUNT_NUMBER = os.environ['TT_ACCOUNT']
TASTYTRADE_PAPER_ACCOUNT = os.environ.get('TT_PAPER_ACCOUNT') or None  # None = live trading

FILL_WAIT_SECS = 120
