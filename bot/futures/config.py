# bot/futures/config.py
import os
from dotenv import load_dotenv

load_dotenv()

FUTURES_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'futures.db')

SYMBOLS = ['ES', 'NQ', 'RTY']

TICK_INFO = {
    'ES':  {'tick': 0.25, 'tick_value': 12.50, 'point_value': 50.0},
    'NQ':  {'tick': 0.25, 'tick_value':  5.00, 'point_value': 20.0},
    'RTY': {'tick': 0.10, 'tick_value':  5.00, 'point_value': 50.0},
}

STRATEGY_PARAMS = {
    'vwap_deviation_pct': 0.15,
    'orb_minutes': 30,
    'orb_min_range_ticks': 8,
}

RISK_RULES = {
    'stop_ticks': 8,
    'target_ticks': 16,
    'max_contracts': 2,
    'daily_loss_limit': 500.0,
    'news_blackout_minutes': 5,
}

TIMEZONE    = 'America/New_York'
MARKET_OPEN = '09:30'
MARKET_CLOSE= '16:00'
ORB_END     = '10:00'

TV_USERNAME  = os.environ.get('TV_USERNAME', '')
TV_PASSWORD  = os.environ.get('TV_PASSWORD', '')
TV_CID       = os.environ.get('TV_CID', '')
TV_SEC       = os.environ.get('TV_SEC', '')
TV_DEVICE_ID = os.environ.get('TV_DEVICE_ID', 'sharp-bot-futures-001')
AV_API_KEY   = os.environ.get('AV_API_KEY', '')
TV_DEMO      = os.environ.get('TV_DEMO', 'true').lower() == 'true'

BASE_URL = 'https://demo.tradovateapi.com/v1' if TV_DEMO else 'https://live.tradovateapi.com/v1'
WS_URL   = 'wss://md.tradovateapi.com/v1/websocket'
