# Options Trading Bot — Alpha Design Spec
**Date:** 2026-05-11  
**Status:** Approved

---

## Overview

Automated options trading bot that scans SPY/QQQ/IWM for high-probability setups, places Bull Put Spreads and Iron Condors via the Tastytrade API, manages open positions, and closes at defined targets/stops. The Sharp Bot website's Options Trading tab serves as the dashboard — no Discord integration.

---

## Architecture

Python bot runs as a standalone process (APScheduler). Node.js web server reads from a shared SQLite database. No network calls between the two processes.

```
Kalshi-Bot/
  bot/
    main.py            # entry point — starts scheduler, runs until killed
    config.py          # tickers, rules, position sizing, credentials
    tastytrade.py      # Tastytrade API client (auth, chain, orders)
    scanner.py         # finds qualifying setups every 15 min
    trader.py          # builds and places spread orders
    manager.py         # monitors positions, fires closes
    db.py              # SQLite read/write helpers
    requirements.txt
    data/
      options.db       # shared with Node.js
  web/
    routes/options.js  # reads SQLite, serves dashboard data
    views/options.ejs  # full dashboard UI
```

---

## SQLite Schema

**`scans`** — every qualifying setup the scanner finds, whether traded or not
```sql
CREATE TABLE scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  underlying TEXT,           -- 'SPY' | 'QQQ' | 'IWM'
  strategy TEXT,             -- 'bull_put_spread' | 'iron_condor'
  expiration TEXT,           -- 'YYYY-MM-DD'
  short_put_strike REAL,
  long_put_strike REAL,
  short_call_strike REAL,    -- NULL for bull put spread
  long_call_strike REAL,     -- NULL for bull put spread
  credit REAL,               -- total credit in dollars
  width REAL,                -- spread width in dollars
  delta REAL,                -- short strike delta
  iv_rank REAL,              -- 0–100
  dte INTEGER,
  traded INTEGER DEFAULT 0   -- 1 if this led to a placed trade
);
```

**`trades`** — placed positions, open and closed
```sql
CREATE TABLE trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER,
  underlying TEXT,
  strategy TEXT,
  expiration TEXT,
  short_put_strike REAL,
  long_put_strike REAL,
  short_call_strike REAL,    -- NULL for bull put spread
  long_call_strike REAL,     -- NULL for bull put spread
  entry_credit REAL,
  entry_ts TEXT,
  close_credit REAL,         -- NULL while open
  close_ts TEXT,             -- NULL while open
  close_reason TEXT,         -- 'profit_target' | 'stop_loss' | 'dte_expire' | 'manual'
  status TEXT DEFAULT 'open',-- 'open' | 'closed' | 'pending' | 'cancelled'
  contracts INTEGER DEFAULT 1,
  order_id TEXT              -- Tastytrade order ID
);
```

**`account_snapshots`** — hourly balance/P&L snapshots
```sql
CREATE TABLE account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  net_liq REAL,
  cash REAL,
  open_pnl REAL,
  realized_pnl_today REAL
);
```

---

## Bot Logic

### Scanner (every 15 min, 9:45am–3:45pm ET, weekdays)

For each of SPY, QQQ, IWM:
1. Fetch options chain via Tastytrade API
2. Filter expirations: DTE 21–45
3. Compute IV rank (current IV vs trailing 52-week high/low)
4. Skip if IV rank < 30
5. Find puts with delta ≤ 0.30 → candidate short put strikes
6. **Bull Put Spread**: for each candidate, pair with a lower strike; check credit ≥ 1/3 of width
7. **Iron Condor**: pair the put spread with a call spread (call delta ≤ 0.30); check combined credit ≥ 1/3 of width
8. Write all qualifying setups to `scans`

### Trader (called by scanner on qualifying setup)

- Skip if an open trade already exists for that underlying
- Position sizing: `floor(account_net_liq * 0.05 / (width * 100))` contracts (max 5% of account)
- Place limit order at mid-price
- If unfilled after 2 min → reprice to natural (ask side)
- If unfilled after 2 more min → cancel, log as `cancelled`
- On fill → write to `trades`, set `scans.traded = 1`

### Manager (every 5 min, market hours)

For each open trade:
- Fetch current mark price of the spread from Tastytrade
- Calculate P&L%: `(entry_credit - current_mark) / entry_credit * 100`
- **Close if**: P&L ≥ 50%, or loss ≥ 200% of credit, or DTE ≤ 7
- Place closing buy-back order at mid, same 2-min retry logic
- On fill → update `trades`: `close_credit`, `close_ts`, `close_reason`, `status = 'closed'`

### Account Snapshots (every 1 hour, market hours)

Fetch net liquidation, cash, open P&L from Tastytrade account endpoint → write to `account_snapshots`.

---

## Dashboard UI (web/views/options.ejs)

**Nav change:** Replace standalone `<a href="/options">Options Trading</a>` with a new "Options Trading ▼" dropdown across all pages. Alpha has one item: Dashboard (`/options`). Dropdown grows as pages are added.

**Four panels:**

1. **Account Summary** (top bar)  
   Net liq · Cash available · Open P&L · Realized P&L today · All-time realized P&L

2. **Open Positions** (main table)  
   Underlying | Strategy | Strikes | Expiration | DTE | Entry Credit | Current Value | P&L $ | P&L % | Status  
   Color-coded rows: green (profitable) / red (losing) / yellow (pending fill)

3. **Scanner Feed** (below positions)  
   Last 20 scans, newest first. Columns: Time | Underlying | Strategy | Strikes | Credit | IV Rank | Delta | DTE  
   "Traded" badge on rows that became positions.

4. **Trade History** (collapsible, bottom)  
   Closed trades sorted newest first. Same columns as Open Positions + Close Reason + Final P&L.

Page auto-refreshes every 60 seconds via `<meta http-equiv="refresh" content="60">`.

---

## Configuration (bot/config.py)

```python
UNDERLYINGS = ['SPY', 'QQQ', 'IWM']

ENTRY_RULES = {
    'max_delta': 0.30,
    'min_iv_rank': 30,
    'min_dte': 21,
    'max_dte': 45,
    'min_credit_to_width_ratio': 1/3,
}

EXIT_RULES = {
    'profit_target_pct': 50,   # close at 50% of max profit
    'stop_loss_pct': 200,      # close at 2x credit received
    'dte_close': 7,            # always close inside 7 DTE
}

POSITION_SIZING = {
    'max_pct_per_trade': 0.05,  # 5% of net liq per trade
}

MARKET_HOURS = {
    'open': '09:45',
    'close': '15:45',
    'timezone': 'America/New_York',
}

# Tastytrade credentials loaded from environment variables:
# TASTYTRADE_USERNAME, TASTYTRADE_PASSWORD, TASTYTRADE_ACCOUNT_NUMBER
```

---

## Dependencies

```
tastytrade       # official Tastytrade Python SDK
APScheduler      # job scheduling
pytz             # timezone handling
```

No Flask/FastAPI needed — communication is purely via SQLite.

---

## Out of Scope (Alpha)

- Paper trading / sandbox mode (Tastytrade sandbox is available but not wired up in alpha)
- Multiple accounts
- Position adjustments / rolls
- Greeks dashboard
- Mobile push notifications
- Backtesting
