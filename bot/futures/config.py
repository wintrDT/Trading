# bot/futures/config.py
import os
from dotenv import load_dotenv

load_dotenv()

FUTURES_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'futures.db')

SYMBOLS = ['ES', 'NQ']

TICK_INFO = {
    'ES': {'tick': 0.25, 'tick_value': 12.50, 'point_value':  50.0},
    'NQ': {'tick': 0.25, 'tick_value':  5.00, 'point_value':  20.0},
    'GC': {'tick': 0.10, 'tick_value': 10.00, 'point_value': 100.0},
}

STRATEGY_PARAMS = {
    'vwap_deviation_pct': 0.15,   # default fallback
    'orb_minutes': 30,
    'orb_min_range_ticks': 8,
}

# Per-symbol VWAP thresholds — tuned to each instrument's volatility
SYMBOL_VWAP_PCT = {
    'ES': 0.05,   # S&P — ~4 pts deviation needed
    'NQ': 0.05,   # Nasdaq — ~15 pts deviation needed
    'GC': 0.15,   # Gold — ~7 pts deviation needed
}

# Per-symbol news keywords — what actually moves each instrument
SYMBOL_NEWS_KEYWORDS = {
    'ES':  {'bull': {'jobs', 'hiring', 'gdp', 'growth', 'earnings', 'beat', 'rally', 'strong'},
            'bear': {'recession', 'layoffs', 'miss', 'weak', 'tariff', 'selloff', 'decline', 'fears'}},
    'NQ':  {'bull': {'ai', 'tech', 'earnings', 'beat', 'innovation', 'growth', 'upgrade', 'record'},
            'bear': {'antitrust', 'regulation', 'miss', 'rates', 'selloff', 'downgrade', 'weak'}},
    'GC':  {'bull': {'inflation', 'war', 'geopolit', 'uncertainty', 'dollar falls', 'fed cuts', 'safe haven'},
            'bear': {'dollar rises', 'rate hike', 'yields', 'risk on', 'strong economy', 'fed hikes'}},
}

RISK_RULES = {
    'stop_ticks': 8,
    'target_ticks': 16,
    'max_contracts': 2,
    'daily_loss_limit': 2000.0,  # bot stops trading for the day at -$2000
    'news_blackout_minutes': 5,
    'trade_timeout_minutes': 30,
    'cooldown_minutes': 1,
    # Fast-fail filter: if a trade is older than X seconds AND has NEVER shown
    # positive MFE AND is currently >$Y underwater, close it early. Yesterday's
    # data showed winners have avg MAE -$9 while losers have avg MAE -$262.
    # Trades that don't go green within ~2 min are structurally lost causes.
    'fast_fail_min_age_sec': 120,
    'fast_fail_max_neg_usd': -50.0,
}

# TopStep funded/eval account rules. The bot enforces these BEFORE TopStep does
# so we never get an account terminated for a rule violation we could have
# avoided. Values here are conservative — well below actual plan limits.
#
# TopStep Combine plans (as of 2026):
#   $50k  Combine: max  5 contracts, daily loss $1,000, trailing DD $2,000
#   $100k Combine: max 10 contracts, daily loss $2,000, trailing DD $3,000
#   $150k Combine: max 15 contracts, daily loss $3,000, trailing DD $4,500
TOPSTEP_RULES = {
    'plan':              '50k',     # active plan — '50k' / '100k' / '150k'
    'max_contracts':     2,         # hard cap (start small, well under plan's 5)
    'daily_loss_limit':  2000.0,    # bot stops trading for the day at -$2000
    'trailing_drawdown': 2000.0,    # TopStep $50k plan max loss (trailing) — match exactly
    'eod_flat_hour_et':  16,        # close all positions at 4:00 PM ET (TopStep cutoff is 4:10 PM ET / 3:10 PM CT)
    'eod_flat_minute':   0,
}

TUNE_BOUNDS = {
    'long':  {'rsi': {'min': 30, 'max': 65}},
    'short': {'rsi': {'min': 45, 'max': 70}},
    'dev':   {'min': 0.05, 'max': 0.25},
}

# Per-symbol stop/target overrides — GC noise range is $1-2/scan, 8-tick stop ($0.80) is too tight
SYMBOL_RISK = {
    # short_target_ticks = $400 profit per contract (ES: 32×$12.50, NQ: 80×$5.00)
    # counter_trend_target_ticks = $200 (half, since fighting the trend)
    'ES': {'stop_ticks': 8,  'target_ticks': 16, 'short_stop_ticks': 12, 'short_target_ticks': 32, 'counter_trend_target_ticks': 16},
    'NQ': {'stop_ticks': 8,  'target_ticks': 16, 'short_stop_ticks': 12, 'short_target_ticks': 80, 'counter_trend_target_ticks': 40},
    'GC': {'stop_ticks': 20, 'target_ticks': 40, 'short_stop_ticks': 20, 'short_target_ticks': 40, 'counter_trend_target_ticks': 20},
}

# Per-symbol blocked ET hours — block entries during hours that historically lose money.
# 09:00 + 14-16 ET removed for re-test after the trailing-stop bug fix (those losses
# were partly trail-bug victims, not bad-hour victims). Only 22-23 ET kept blocked —
# at 22:00 the bot was direction-wrong 6/7 times (14% WR), which is regime, not trail.
BLOCKED_HOURS_ET = {
    'ES': {13, 22, 23},  # 13 added — lunch chop (-$445 yesterday, 38% WR)
    'NQ': set(),
}

TIMEZONE    = 'America/New_York'
# Futures close only 5–6 PM ET daily; ORB is the first 30 min of the regular session
MARKET_CLOSE_HOUR = 17   # 5 PM ET — maintenance window starts
MARKET_OPEN_HOUR  = 18   # 6 PM ET — new session begins
ORB_START = '09:30'
ORB_END   = '09:45'

TV_USERNAME  = os.environ.get('TV_USERNAME', '')
TV_PASSWORD  = os.environ.get('TV_PASSWORD', '')
TV_CID       = os.environ.get('TV_CID', '')
TV_SEC       = os.environ.get('TV_SEC', '')
TV_DEVICE_ID = os.environ.get('TV_DEVICE_ID', 'sharp-bot-futures-001')
AV_API_KEY   = os.environ.get('AV_API_KEY', '')
TV_DEMO      = os.environ.get('TV_DEMO', 'true').lower() == 'true'

BASE_URL = 'https://demo.tradovateapi.com/v1' if TV_DEMO else 'https://live.tradovateapi.com/v1'
WS_URL   = 'wss://md.tradovateapi.com/v1/websocket'
