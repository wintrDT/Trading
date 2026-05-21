# bot/futures/config.py
import os
from dotenv import load_dotenv

load_dotenv()

FUTURES_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'futures.db')

SYMBOLS = ['ES', 'NQ']

TICK_INFO = {
    'ES': {'tick': 0.25, 'tick_value': 12.50, 'point_value':  50.0},
    'NQ': {'tick': 0.25, 'tick_value':  5.00, 'point_value':  20.0},
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
    'daily_profit_target': 0,       # DISABLED (paper trading) — 0 makes the `if profit_target` guard a no-op so the bot keeps trading all session for data. Restore to 3000 before going live.
    'news_blackout_minutes': 5,
    'trade_timeout_minutes': 30,
    'cooldown_minutes': 2,  # base re-entry wait per symbol (x3 after a stop_loss) — cuts churn/fees
    # Fast-fail filter: if a trade is older than X seconds AND has NEVER shown
    # positive MFE AND is currently >$Y underwater, close it early.
    # Grace period extended 120 -> 300s — ES/NQ trades often need 5-10 min to develop.
    'fast_fail_min_age_sec': 300,
    'fast_fail_max_neg_usd': -50.0,
    # Minimum confidence score 0-100 required to enter a trade.
    'min_confidence': 60,
    # Override threshold — when confidence >= this value, single-indicator filters
    # (SMA trend, day-type direction, micro-momentum) AND bounce confirmation
    # are bypassed. Raised 75 -> 85 so override is genuinely rare; only
    # the strongest composite setups skip the safety stack.
    'confidence_override': 85,
    # Trend-pullback entry-quality gates (added 2026-05-20 after a string of trend
    # losses). A real uptrend pullback has healthy momentum and isn't overextended;
    # the losers had RSI 19/32/34/36 and dev 0.27-0.39% (collapses, not dips).
    'trend_min_rsi_long':  45,    # skip trend LONG if RSI below this (weak/falling)
    'trend_max_rsi_short': 55,    # skip trend SHORT if RSI above this
    'trend_max_dev_pct':   0.15,  # skip trend entry if |price-VWAP| deviation exceeds this % (overextended)
    # Reversion RSI gate (revised 2026-05-20 from real data, not a single trade).
    # Shorts get progressively worse as RSI rises: RSI 60-70 = -$69/trade,
    # 70+ = -$36, while RSI <60 shorts are net positive. So block shorts only when
    # RSI is high (fighting strong up-momentum). Longs are profitable across the RSI
    # range (deep-oversold AND momentum-recovery), so they get NO RSI block.
    'reversion_rsi_short_max': 60,  # skip reversion SHORT if RSI at/above this (up-momentum too strong)
    # Long ceiling set HIGH (not the earlier wrong 60). Longs profit through RSI 75
    # (60-75 = +$60/trade lifetime) but collapse above it: RSI 75-85 = -$78/trade,
    # 85-101 = -$109 at 12% WR (buying the top of an overbought rip). Block >= 80.
    'reversion_rsi_long_max':  80,  # skip reversion LONG if RSI at/above this (overbought rip, top risk)
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
    'plan':                '50k-V2',   # active plan — 50k V2 Combine
    'max_contracts':       2,          # hard cap (start small, well under plan's 5)
    'start_balance':       50000.0,    # Combine starting balance (for trailing-floor + profit-target math)
    'trailing_drawdown':   2000.0,     # 50k V2 max loss (trailing from peak) — HARD account-kill limit
    'trailing_halt_buffer': 400.0,     # halt this far BEFORE the floor so we never actually breach
    'profit_target_total': 3000.0,     # Combine passes at +$3000 from start (balance >= $53000) — halt to lock the pass
    'eod_flat_hour_et':    16,         # close all positions at 4:00 PM ET (TopStep cutoff is 4:10 PM ET / 3:10 PM CT)
    'eod_flat_minute':     0,
}

# Per-symbol max contracts. NQ moves more points per bar than ES, so the same
# contract count produces larger swings — losses AND wins. Cap NQ at 1 to halve
# its exposure while keeping it trading (user: "dont disable nq"). Symbols not
# listed use the global RISK_RULES/TOPSTEP_RULES max_contracts.
SYMBOL_MAX_CONTRACTS = {
    'NQ': 1,
}

ENABLE_TREND_STRATEGY = True

# Real volume-weighted VWAP. When True, VWAP is rebuilt each ~60s from 1-min bar
# volume (the institutional anchor) instead of an unweighted price average fed
# volume=1 per scan. Requires a broker client exposing get_bars(); falls back to
# the unweighted average if bars aren't available. NOTE: deviation thresholds
# (SYMBOL_VWAP_PCT, tuned devs) were fit against the old proxy — watch paper
# dev_pct distribution after enabling and recalibrate if needed.
USE_REAL_VWAP = True

# Strategy: Volume-Weighted Exhaustion Fade. Fades a fresh N-bar extreme that
# printed on BELOW-average volume (a move with no fuel reverts toward VWAP); the
# same extreme on rising volume is left alone. Uses the 1-min bars already fetched
# for the real VWAP. Defaults are starting points — tune from paper data.
ENABLE_EXHAUSTION_FADE = True
EXH_FADE_LOOKBACK_BARS  = 20     # bars defining the "fresh extreme" window
EXH_FADE_VOL_MULT       = 0.7    # extreme bar volume must be < this * 20-bar avg volume
EXH_FADE_MIN_DEV_PCT    = 0.10   # price must be at least this % from VWAP to fade

# Shorts have a much weaker edge than longs (lifetime exp +$17.6 vs +$59/trade)
# in this generally-bullish regime. Require shorts to be more extended from VWAP
# than longs before entering — a higher bar filters out marginal counter-rallies.
SHORT_DEV_MULTIPLIER = 1.3

# Strong-trend day types where VWAP reversion has little edge and gets whipsawed.
# On these days the bot scales reversion size down to the minimum (1 contract)
# instead of forcing normal size into a market that isn't reverting.
STRONG_TREND_DAY_TYPES = {'trend_day_up', 'trend_day_down', 'gap_and_go_up', 'gap_and_go_down'}

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
    # Updated 2026-05-19 after 521-trade hourly audit.
    # Removed 13 ET (was -$445 pre-trail-fix; full audit shows +$2,400 net, $49/trade).
    # Added 7, 8 (pre-NY chop, -$1,272 combined), 16 (close chop, -$468),
    #       19, 20 (evening transition, -$655 — caused tonight's live loss),
    #       22, 23 (overnight transition, kept).
    # KEEP TRADING: 9-15 ET (NY day), 21 ET (Asia open), 0-6 ET (overnight) — all profitable.
    'ES': {7, 8, 16, 19, 20, 22, 23},
    'NQ': {7, 8, 16, 19, 20, 22, 23},
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
