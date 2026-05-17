# bot/crypto/config.py
import os
from dotenv import load_dotenv

load_dotenv()

CRYPTO_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'crypto.db')

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT']

DISPLAY_NAMES = {
    'BTCUSDT':  'BTC',
    'ETHUSDT':  'ETH',
    'SOLUSDT':  'SOL',
    'DOGEUSDT': 'DOGE',
}

# Percentage-based stops/targets (more natural for crypto than ticks)
RISK_RULES = {
    'stop_pct':           0.40,
    'target_pct':         0.50,   # tightened — was 0.80, mostly unreachable in 30m
    'max_positions':      4,
    'trade_timeout_minutes': 90,  # was 30 — let reversion plays out
    'daily_loss_limit':   999999.0,
    'position_size_usd':  2000.0, # was 500 — 4x dollar P&L per trade (sim)
}

STRATEGY_PARAMS = {
    'vwap_deviation_pct':    0.15,   # fire when 0.15%+ from VWAP
    'channel_min_width_pct': 0.05,   # allow slightly tighter channels
    'breakeven_trigger_pct': 0.50,   # trail to BE at 50% of target
    'counter_trend_target_mult': 0.5,  # counter-trend trades use half target (fight trend = smaller win)
}

# Per-symbol overrides — noisier coins need proportionally more room
# Targets tightened to be hittable within the 90-min timeout window
SYMBOL_OVERRIDES = {
    'DOGEUSDT': {'stop_pct': 0.60, 'target_pct': 0.80},  # was 1.20
    'SOLUSDT':  {'stop_pct': 0.50, 'target_pct': 0.65},  # was 1.00
    'BTCUSDT':  {'stop_pct': 0.35, 'target_pct': 0.45},  # was 0.70
}
