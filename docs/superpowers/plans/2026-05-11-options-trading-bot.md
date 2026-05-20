# Options Trading Bot — Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated Bull Put Spread / Iron Condor bot on SPY/QQQ/IWM using the Tastytrade API, with the Sharp Bot website's Options Trading tab as the live dashboard.

**Architecture:** Python bot (APScheduler) handles scanning, order placement, and position management, writing all state to a shared SQLite file (`bot/data/options.db`). The existing Node.js Express server reads from that same file on each page request — no network calls between processes. Dashboard auto-refreshes every 60 seconds.

**Tech Stack:** Python 3.11+, tastytrade SDK, APScheduler 3.x, pytz, Node.js (existing), better-sqlite3 (already in package.json)

---

## File Map

| File | Create / Modify | Purpose |
|------|----------------|---------|
| `bot/__init__.py` | Create | Package marker (empty) |
| `bot/requirements.txt` | Create | Python dependencies |
| `bot/data/.gitkeep` | Create | Ensures data dir is tracked |
| `bot/config.py` | Create | Tickers, entry/exit rules, credentials from env |
| `bot/db.py` | Create | SQLite init + all read/write helpers |
| `bot/tt_client.py` | Create | Tastytrade API wrapper (session, chain, account, orders) |
| `bot/scanner.py` | Create | IV rank filter, BPS + IC setup finders |
| `bot/trader.py` | Create | Position sizing, order placement, fill retry |
| `bot/manager.py` | Create | Position monitoring, exit rule evaluation, close orders |
| `bot/main.py` | Create | APScheduler entry point, wires all jobs |
| `tests/__init__.py` | Create | Package marker |
| `tests/test_db.py` | Create | DB schema + helper tests |
| `tests/test_scanner.py` | Create | Scanner filtering logic tests |
| `tests/test_trader.py` | Create | Position sizing + leg-building tests |
| `tests/test_manager.py` | Create | Exit rule evaluation tests |
| `web/routes/options.js` | Modify | Read SQLite, pass data to template |
| `web/views/options.ejs` | Modify | Replace "coming soon" with full dashboard |
| `web/views/dashboard.ejs` | Modify | Replace standalone Options link with dropdown |
| `web/views/spreads.ejs` | Modify | Same nav change |
| `web/views/history.ejs` | Modify | Same nav change |
| `web/views/portfolio.ejs` | Modify | Same nav change |
| `web/views/settings.ejs` | Modify | Same nav change |
| `web/views/admin.ejs` | Modify | Same nav change |

---

## Task 1: Project Scaffold + Database

**Files:**
- Create: `bot/__init__.py`, `bot/requirements.txt`, `bot/data/.gitkeep`
- Create: `bot/db.py`
- Create: `tests/__init__.py`, `tests/test_db.py`

- [ ] **Step 1: Create bot package structure**

```bash
cd "c:\Users\shayn\Desktop\Kalshi-Bot"
mkdir bot
mkdir bot\data
New-Item -ItemType File bot\__init__.py
New-Item -ItemType File bot\data\.gitkeep
mkdir tests
New-Item -ItemType File tests\__init__.py
```

- [ ] **Step 2: Create `bot/requirements.txt`**

```
tastytrade>=8.0
APScheduler>=3.10
pytz>=2024.1
```

- [ ] **Step 3: Install Python dependencies**

```bash
cd "c:\Users\shayn\Desktop\Kalshi-Bot\bot"
pip install -r requirements.txt
```

Expected: All three packages install with no errors.

- [ ] **Step 4: Write failing tests in `tests/test_db.py`**

```python
import sqlite3
import pytest
from bot.db import (
    init_db, insert_scan, insert_trade,
    update_trade_closed, update_trade_status, update_trade_order_id,
    mark_scan_traded, insert_account_snapshot,
    get_open_trades, get_recent_scans, get_all_trades, get_account_snapshot,
)

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path

SAMPLE_SCAN = {
    'ts': '2026-05-11T10:00:00',
    'underlying': 'SPY',
    'strategy': 'bull_put_spread',
    'expiration': '2026-06-06',
    'short_put_strike': 520.0,
    'long_put_strike': 515.0,
    'short_call_strike': None,
    'long_call_strike': None,
    'credit': 1.75,
    'width': 5.0,
    'delta': 0.25,
    'iv_rank': 45.0,
    'dte': 26,
    'traded': 0,
}

SAMPLE_TRADE = {
    'underlying': 'SPY',
    'strategy': 'bull_put_spread',
    'expiration': '2026-06-06',
    'short_put_strike': 520.0,
    'long_put_strike': 515.0,
    'short_call_strike': None,
    'long_call_strike': None,
    'entry_credit': 1.75,
    'entry_ts': '2026-05-11T10:05:00',
    'contracts': 1,
    'order_id': 'TT-123456',
}

def test_init_creates_tables(db_path):
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert tables == {'scans', 'trades', 'account_snapshots'}

def test_insert_and_retrieve_scan(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    assert scan_id is not None
    scans = get_recent_scans(db_path, limit=10)
    assert len(scans) == 1
    assert scans[0]['underlying'] == 'SPY'
    assert scans[0]['credit'] == 1.75

def test_insert_trade_appears_in_open(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    assert trade_id is not None
    trades = get_open_trades(db_path)
    assert len(trades) == 1
    assert trades[0]['order_id'] == 'TT-123456'

def test_update_trade_closed_removes_from_open(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_closed(db_path, trade_id,
                        close_credit=0.875,
                        close_reason='profit_target',
                        close_ts='2026-05-11T14:00:00')
    assert get_open_trades(db_path) == []
    all_trades = get_all_trades(db_path)
    assert all_trades[0]['close_reason'] == 'profit_target'

def test_update_trade_status(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_status(db_path, trade_id, 'pending')
    trades = get_open_trades(db_path)
    assert trades[0]['status'] == 'pending'

def test_update_trade_order_id(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    trade_id = insert_trade(db_path, {**SAMPLE_TRADE, 'scan_id': scan_id})
    update_trade_order_id(db_path, trade_id, 'TT-999999')
    trades = get_open_trades(db_path)
    assert trades[0]['order_id'] == 'TT-999999'

def test_mark_scan_traded(db_path):
    scan_id = insert_scan(db_path, SAMPLE_SCAN)
    mark_scan_traded(db_path, scan_id)
    scans = get_recent_scans(db_path)
    assert scans[0]['traded'] == 1

def test_account_snapshot(db_path):
    insert_account_snapshot(db_path, {
        'ts': '2026-05-11T10:00:00',
        'net_liq': 25000.0,
        'cash': 20000.0,
        'open_pnl': 150.0,
        'realized_pnl_today': 75.0,
    })
    snap = get_account_snapshot(db_path)
    assert snap['net_liq'] == 25000.0

def test_get_account_snapshot_none_when_empty(db_path):
    assert get_account_snapshot(db_path) is None
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd "c:\Users\shayn\Desktop\Kalshi-Bot"
python -m pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.db'`

- [ ] **Step 6: Implement `bot/db.py`**

```python
import sqlite3

_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    underlying TEXT,
    strategy TEXT,
    expiration TEXT,
    short_put_strike REAL,
    long_put_strike REAL,
    short_call_strike REAL,
    long_call_strike REAL,
    credit REAL,
    width REAL,
    delta REAL,
    iv_rank REAL,
    dte INTEGER,
    traded INTEGER DEFAULT 0
)
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER,
    underlying TEXT,
    strategy TEXT,
    expiration TEXT,
    short_put_strike REAL,
    long_put_strike REAL,
    short_call_strike REAL,
    long_call_strike REAL,
    entry_credit REAL,
    entry_ts TEXT,
    close_credit REAL,
    close_ts TEXT,
    close_reason TEXT,
    status TEXT DEFAULT 'open',
    contracts INTEGER DEFAULT 1,
    order_id TEXT
)
"""

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    net_liq REAL,
    cash REAL,
    open_pnl REAL,
    realized_pnl_today REAL
)
"""


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    with _conn(db_path) as conn:
        conn.execute(_CREATE_SCANS)
        conn.execute(_CREATE_TRADES)
        conn.execute(_CREATE_SNAPSHOTS)


def insert_scan(db_path, scan):
    sql = """
    INSERT INTO scans (ts, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        credit, width, delta, iv_rank, dte, traded)
    VALUES (:ts, :underlying, :strategy, :expiration,
        :short_put_strike, :long_put_strike, :short_call_strike, :long_call_strike,
        :credit, :width, :delta, :iv_rank, :dte, :traded)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, scan).lastrowid


def insert_trade(db_path, trade):
    sql = """
    INSERT INTO trades (scan_id, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        entry_credit, entry_ts, contracts, order_id, status)
    VALUES (:scan_id, :underlying, :strategy, :expiration,
        :short_put_strike, :long_put_strike, :short_call_strike, :long_call_strike,
        :entry_credit, :entry_ts, :contracts, :order_id, 'open')
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, trade).lastrowid


def update_trade_closed(db_path, trade_id, close_credit, close_reason, close_ts):
    sql = """UPDATE trades
             SET close_credit=?, close_ts=?, close_reason=?, status='closed'
             WHERE id=?"""
    with _conn(db_path) as conn:
        conn.execute(sql, (close_credit, close_ts, close_reason, trade_id))


def update_trade_status(db_path, trade_id, status):
    with _conn(db_path) as conn:
        conn.execute("UPDATE trades SET status=? WHERE id=?", (status, trade_id))


def update_trade_order_id(db_path, trade_id, order_id):
    with _conn(db_path) as conn:
        conn.execute("UPDATE trades SET order_id=? WHERE id=?", (order_id, trade_id))


def mark_scan_traded(db_path, scan_id):
    with _conn(db_path) as conn:
        conn.execute("UPDATE scans SET traded=1 WHERE id=?", (scan_id,))


def insert_account_snapshot(db_path, snap):
    sql = """
    INSERT INTO account_snapshots (ts, net_liq, cash, open_pnl, realized_pnl_today)
    VALUES (:ts, :net_liq, :cash, :open_pnl, :realized_pnl_today)
    """
    with _conn(db_path) as conn:
        conn.execute(sql, snap)


def get_open_trades(db_path):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status IN ('open','pending') ORDER BY entry_ts DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_scans(db_path, limit=20):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_trades(db_path):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_account_snapshot(db_path):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_db.py -v
```

Expected: 9 tests PASS

- [ ] **Step 8: Commit**

```bash
git add bot/__init__.py bot/requirements.txt bot/data/.gitkeep bot/db.py tests/__init__.py tests/test_db.py
git commit -m "feat: add SQLite schema and db helpers"
```

---

## Task 2: Configuration

**Files:**
- Create: `bot/config.py`

- [ ] **Step 1: Create `bot/config.py`**

```python
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

TASTYTRADE_USERNAME = os.environ['TASTYTRADE_USERNAME']
TASTYTRADE_PASSWORD = os.environ['TASTYTRADE_PASSWORD']
TASTYTRADE_ACCOUNT_NUMBER = os.environ['TASTYTRADE_ACCOUNT_NUMBER']
```

- [ ] **Step 2: Commit**

```bash
git add bot/config.py
git commit -m "feat: add bot configuration"
```

---

## Task 3: Tastytrade API Client

**Files:**
- Create: `bot/tt_client.py`
- Create: `tests/test_tt_client.py`

This module is a thin wrapper around the `tastytrade` SDK. All SDK imports live here so the rest of the bot can be tested with mocks.

- [ ] **Step 1: Write failing tests in `tests/test_tt_client.py`**

```python
from unittest.mock import MagicMock, patch
from bot.tt_client import TastytradeClient


@patch('bot.tt_client.Session')
@patch('bot.tt_client.Account')
def test_connect_sets_session_and_account(mock_account_cls, mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_account = MagicMock()
    mock_account.account_number = 'ACC123'
    mock_account_cls.get_accounts.return_value = [mock_account]

    client = TastytradeClient('user', 'pass', 'ACC123')
    client.connect()

    assert client.session is mock_session
    assert client.account is mock_account


@patch('bot.tt_client.Session')
@patch('bot.tt_client.Account')
@patch('bot.tt_client.get_market_metrics')
def test_get_iv_rank_converts_to_0_100(mock_metrics, mock_account_cls, mock_session_cls):
    mock_session_cls.return_value = MagicMock()
    mock_account = MagicMock()
    mock_account.account_number = 'ACC123'
    mock_account_cls.get_accounts.return_value = [mock_account]

    mock_metric = MagicMock()
    mock_metric.implied_volatility_index_rank = 0.42
    mock_metrics.return_value = [mock_metric]

    client = TastytradeClient('user', 'pass', 'ACC123')
    client.connect()
    iv_rank = client.get_iv_rank('SPY')

    assert iv_rank == 42.0


@patch('bot.tt_client.Session')
@patch('bot.tt_client.Account')
@patch('bot.tt_client.get_market_metrics')
def test_get_iv_rank_returns_none_when_empty(mock_metrics, mock_account_cls, mock_session_cls):
    mock_session_cls.return_value = MagicMock()
    mock_account_cls.get_accounts.return_value = [MagicMock()]
    mock_metrics.return_value = []

    client = TastytradeClient('user', 'pass', 'ACC123')
    client.connect()

    assert client.get_iv_rank('SPY') is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_tt_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.tt_client'`

- [ ] **Step 3: Implement `bot/tt_client.py`**

```python
from decimal import Decimal
from tastytrade import Session, Account
from tastytrade.instruments import NestedOptionChain
from tastytrade.metrics import get_market_metrics
from tastytrade.orders import (
    NewOrder, OrderAction, OrderType, OrderTimeInForce,
    Leg, InstrumentType, PriceEffect,
)


class TastytradeClient:
    def __init__(self, username, password, account_number):
        self._username = username
        self._password = password
        self._account_number = account_number
        self.session = None
        self.account = None

    def connect(self):
        self.session = Session(self._username, self._password)
        accounts = Account.get_accounts(self.session)
        self.account = next(
            (a for a in accounts if a.account_number == self._account_number),
            accounts[0],
        )

    def get_iv_rank(self, symbol):
        metrics = get_market_metrics(self.session, [symbol])
        if not metrics:
            return None
        rank = metrics[0].implied_volatility_index_rank
        return float(rank) * 100 if rank is not None else None

    def get_options_chain(self, symbol):
        return NestedOptionChain.get_chain(self.session, symbol)

    def get_account_balance(self):
        return self.account.get_balances(self.session)

    def get_positions(self):
        return self.account.get_positions(self.session)

    def get_order(self, order_id):
        return self.account.get_order(self.session, order_id)

    def cancel_order(self, order_id):
        return self.account.delete_order(self.session, order_id)

    def place_order(self, legs, price_credit):
        order_legs = [
            Leg(
                instrument_type=InstrumentType.EQUITY_OPTION,
                symbol=leg['symbol'],
                quantity=leg['quantity'],
                action=OrderAction[leg['action']],
            )
            for leg in legs
        ]
        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=order_legs,
            price=Decimal(str(round(price_credit, 2))),
            price_effect=PriceEffect.CREDIT,
        )
        return self.account.place_order(self.session, order, dry_run=False)

    def place_debit_order(self, legs, price_debit):
        order_legs = [
            Leg(
                instrument_type=InstrumentType.EQUITY_OPTION,
                symbol=leg['symbol'],
                quantity=leg['quantity'],
                action=OrderAction[leg['action']],
            )
            for leg in legs
        ]
        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=order_legs,
            price=Decimal(str(round(price_debit, 2))),
            price_effect=PriceEffect.DEBIT,
        )
        return self.account.place_order(self.session, order, dry_run=False)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_tt_client.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot/tt_client.py tests/test_tt_client.py
git commit -m "feat: add Tastytrade API client wrapper"
```

---

## Task 4: Scanner

**Files:**
- Create: `bot/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Write failing tests in `tests/test_scanner.py`**

```python
from datetime import date
from unittest.mock import MagicMock
from bot.scanner import (
    calc_dte, passes_iv_rank, passes_delta, passes_credit_ratio,
    build_option_symbol, find_bull_put_spread, find_iron_condor,
)


def test_calc_dte():
    assert calc_dte(date(2026, 6, 6), date(2026, 5, 11)) == 26


def test_passes_iv_rank_above():
    assert passes_iv_rank(45.0) is True


def test_passes_iv_rank_below():
    assert passes_iv_rank(25.0) is False


def test_passes_iv_rank_at_threshold():
    assert passes_iv_rank(30.0) is True


def test_passes_delta_within():
    assert passes_delta(0.25) is True


def test_passes_delta_too_high():
    assert passes_delta(0.35) is False


def test_passes_delta_at_limit():
    assert passes_delta(0.30) is True


def test_passes_credit_ratio_ok():
    # 1.80 / 5.0 = 0.36 > 1/3
    assert passes_credit_ratio(credit=1.80, width=5.0) is True


def test_passes_credit_ratio_too_low():
    # 1.50 / 5.0 = 0.30 < 1/3
    assert passes_credit_ratio(credit=1.50, width=5.0) is False


def test_build_option_symbol_put():
    sym = build_option_symbol('SPY', date(2026, 6, 19), 'P', 520.0)
    assert sym == 'SPY   260619P00520000'


def test_build_option_symbol_call():
    sym = build_option_symbol('SPY', date(2026, 6, 19), 'C', 540.0)
    assert sym == 'SPY   260619C00540000'


def _make_option(strike, delta, bid, ask, opt_type='P'):
    o = MagicMock()
    o.strike_price = strike
    o.delta = delta
    o.bid = bid
    o.ask = ask
    o.option_type = opt_type
    return o


def test_find_bull_put_spread_returns_setup():
    opts = [
        _make_option(520.0, -0.25, 2.00, 2.20),   # short candidate
        _make_option(515.0, -0.15, 0.25, 0.35),   # long candidate
        _make_option(510.0, -0.08, 0.10, 0.15),
    ]
    result = find_bull_put_spread(
        'SPY', date(2026, 6, 6), opts, iv_rank=45.0, today=date(2026, 5, 11)
    )
    assert result is not None
    assert result['strategy'] == 'bull_put_spread'
    assert result['short_put_strike'] == 520.0
    assert result['long_put_strike'] == 515.0
    # credit = mid(520) - mid(515) = 2.10 - 0.30 = 1.80
    assert result['credit'] == 1.80


def test_find_bull_put_spread_skips_low_iv():
    opts = [_make_option(520.0, -0.25, 2.00, 2.20), _make_option(515.0, -0.15, 0.25, 0.35)]
    result = find_bull_put_spread(
        'SPY', date(2026, 6, 6), opts, iv_rank=20.0, today=date(2026, 5, 11)
    )
    assert result is None


def test_find_iron_condor_returns_setup():
    puts = [
        _make_option(520.0, -0.25, 2.00, 2.20, 'P'),
        _make_option(515.0, -0.15, 0.25, 0.35, 'P'),
    ]
    calls = [
        _make_option(560.0, 0.22, 1.80, 2.00, 'C'),
        _make_option(565.0, 0.12, 0.20, 0.30, 'C'),
    ]
    result = find_iron_condor(
        'SPY', date(2026, 6, 6), puts + calls, iv_rank=45.0, today=date(2026, 5, 11)
    )
    assert result is not None
    assert result['strategy'] == 'iron_condor'
    assert result['short_put_strike'] == 520.0
    assert result['short_call_strike'] == 560.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_scanner.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.scanner'`

- [ ] **Step 3: Implement `bot/scanner.py`**

```python
from datetime import date
from bot.config import ENTRY_RULES


def calc_dte(expiration: date, today: date = None) -> int:
    if today is None:
        today = date.today()
    return (expiration - today).days


def passes_iv_rank(iv_rank: float) -> bool:
    return iv_rank >= ENTRY_RULES['min_iv_rank']


def passes_delta(delta: float) -> bool:
    return abs(delta) <= ENTRY_RULES['max_delta']


def passes_credit_ratio(credit: float, width: float) -> bool:
    return credit / width >= ENTRY_RULES['min_credit_to_width_ratio']


def build_option_symbol(underlying: str, expiration: date, opt_type: str, strike: float) -> str:
    exp_str = expiration.strftime('%y%m%d')
    strike_int = int(round(strike * 1000))
    return f"{underlying:<6}{exp_str}{opt_type}{strike_int:08d}"


def _mid(opt) -> float:
    return (float(opt.bid) + float(opt.ask)) / 2


def find_bull_put_spread(underlying, expiration, options, iv_rank, today=None):
    if not passes_iv_rank(iv_rank):
        return None
    if today is None:
        today = date.today()
    dte = calc_dte(expiration, today)
    if not (ENTRY_RULES['min_dte'] <= dte <= ENTRY_RULES['max_dte']):
        return None

    puts = sorted(
        [o for o in options if str(o.option_type).upper() == 'P'],
        key=lambda o: float(o.strike_price),
        reverse=True,
    )

    for short_put in puts:
        if not passes_delta(float(short_put.delta)):
            continue
        short_strike = float(short_put.strike_price)
        short_mid = _mid(short_put)

        for long_put in puts:
            long_strike = float(long_put.strike_price)
            if long_strike >= short_strike:
                continue
            width = short_strike - long_strike
            if width < 1.0:
                continue
            credit = round(short_mid - _mid(long_put), 2)
            if credit <= 0 or not passes_credit_ratio(credit, width):
                continue
            return {
                'underlying': underlying,
                'strategy': 'bull_put_spread',
                'expiration': expiration.isoformat(),
                'short_put_strike': short_strike,
                'long_put_strike': long_strike,
                'short_call_strike': None,
                'long_call_strike': None,
                'credit': credit,
                'width': width,
                'delta': abs(float(short_put.delta)),
                'iv_rank': iv_rank,
                'dte': dte,
            }
    return None


def find_iron_condor(underlying, expiration, options, iv_rank, today=None):
    if not passes_iv_rank(iv_rank):
        return None
    if today is None:
        today = date.today()
    dte = calc_dte(expiration, today)
    if not (ENTRY_RULES['min_dte'] <= dte <= ENTRY_RULES['max_dte']):
        return None

    puts = sorted(
        [o for o in options if str(o.option_type).upper() == 'P'],
        key=lambda o: float(o.strike_price), reverse=True,
    )
    calls = sorted(
        [o for o in options if str(o.option_type).upper() == 'C'],
        key=lambda o: float(o.strike_price),
    )

    put_spread = None
    for sp in puts:
        if not passes_delta(float(sp.delta)):
            continue
        sp_strike = float(sp.strike_price)
        for lp in puts:
            lp_strike = float(lp.strike_price)
            if lp_strike >= sp_strike:
                continue
            width = sp_strike - lp_strike
            if width < 1.0:
                continue
            credit = round(_mid(sp) - _mid(lp), 2)
            if credit > 0:
                put_spread = (sp_strike, lp_strike, credit, width)
                break
        if put_spread:
            break

    call_spread = None
    for sc in calls:
        if not passes_delta(abs(float(sc.delta))):
            continue
        sc_strike = float(sc.strike_price)
        for lc in calls:
            lc_strike = float(lc.strike_price)
            if lc_strike <= sc_strike:
                continue
            width = lc_strike - sc_strike
            if width < 1.0:
                continue
            credit = round(_mid(sc) - _mid(lc), 2)
            if credit > 0:
                call_spread = (sc_strike, lc_strike, credit, width)
                break
        if call_spread:
            break

    if not put_spread or not call_spread:
        return None

    sp_strike, lp_strike, put_credit, put_width = put_spread
    sc_strike, lc_strike, call_credit, call_width = call_spread
    total_credit = round(put_credit + call_credit, 2)
    max_width = max(put_width, call_width)

    if not passes_credit_ratio(total_credit, max_width):
        return None

    return {
        'underlying': underlying,
        'strategy': 'iron_condor',
        'expiration': expiration.isoformat(),
        'short_put_strike': sp_strike,
        'long_put_strike': lp_strike,
        'short_call_strike': sc_strike,
        'long_call_strike': lc_strike,
        'credit': total_credit,
        'width': max_width,
        'delta': ENTRY_RULES['max_delta'],
        'iv_rank': iv_rank,
        'dte': dte,
    }


def scan_underlying(client, underlying, today=None):
    """Return list of qualifying setup dicts for one underlying."""
    if today is None:
        today = date.today()

    iv_rank = client.get_iv_rank(underlying)
    if iv_rank is None or not passes_iv_rank(iv_rank):
        return []

    chain = client.get_options_chain(underlying)
    setups = []

    for exp_obj in chain.expirations:
        exp_date = exp_obj.expiration_date
        if isinstance(exp_date, str):
            exp_date = date.fromisoformat(exp_date)

        dte = calc_dte(exp_date, today)
        if not (ENTRY_RULES['min_dte'] <= dte <= ENTRY_RULES['max_dte']):
            continue

        opts = exp_obj.options
        bps = find_bull_put_spread(underlying, exp_date, opts, iv_rank, today)
        if bps:
            setups.append(bps)

        ic = find_iron_condor(underlying, exp_date, opts, iv_rank, today)
        if ic:
            setups.append(ic)

    return setups
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_scanner.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot/scanner.py tests/test_scanner.py
git commit -m "feat: add options scanner with BPS and IC logic"
```

---

## Task 5: Trader

**Files:**
- Create: `bot/trader.py`
- Create: `tests/test_trader.py`

- [ ] **Step 1: Write failing tests in `tests/test_trader.py`**

```python
from datetime import date
from bot.trader import calc_contracts, build_bps_legs, build_ic_legs


def test_calc_contracts_basic():
    # 25000 * 0.05 = 1250 max risk / (5.0 * 100) = 2.5 → floor = 2
    assert calc_contracts(net_liq=25000, width=5.0) == 2


def test_calc_contracts_large_account():
    # 100000 * 0.05 = 5000 / 500 = 10
    assert calc_contracts(net_liq=100000, width=5.0) == 10


def test_calc_contracts_too_small_returns_zero():
    # 5000 * 0.05 = 250 / 500 = 0.5 → floor = 0 → skip trade
    assert calc_contracts(net_liq=5000, width=5.0) == 0


def test_build_bps_legs_has_two_legs():
    legs = build_bps_legs('SPY', date(2026, 6, 19), 520.0, 515.0, contracts=1)
    assert len(legs) == 2
    actions = {l['action'] for l in legs}
    assert actions == {'SELL_TO_OPEN', 'BUY_TO_OPEN'}


def test_build_bps_legs_sell_is_higher_strike():
    legs = build_bps_legs('SPY', date(2026, 6, 19), 520.0, 515.0, contracts=1)
    sell = next(l for l in legs if l['action'] == 'SELL_TO_OPEN')
    buy = next(l for l in legs if l['action'] == 'BUY_TO_OPEN')
    assert 'P00520000' in sell['symbol']
    assert 'P00515000' in buy['symbol']


def test_build_ic_legs_has_four_legs():
    legs = build_ic_legs(
        'SPY', date(2026, 6, 19),
        short_put=520.0, long_put=515.0,
        short_call=560.0, long_call=565.0,
        contracts=1,
    )
    assert len(legs) == 4
    sell_legs = [l for l in legs if l['action'] == 'SELL_TO_OPEN']
    buy_legs = [l for l in legs if l['action'] == 'BUY_TO_OPEN']
    assert len(sell_legs) == 2
    assert len(buy_legs) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_trader.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.trader'`

- [ ] **Step 3: Implement `bot/trader.py`**

```python
import math
import time
from datetime import date, datetime, timezone
from bot.config import POSITION_SIZING
from bot.scanner import build_option_symbol
from bot.db import (
    insert_trade, update_trade_status, update_trade_order_id,
    mark_scan_traded, get_open_trades,
)

_FILL_WAIT_SECS = 120
_FILL_STATUSES = {'filled', 'partially_filled'}


def calc_contracts(net_liq: float, width: float) -> int:
    max_risk = net_liq * POSITION_SIZING['max_pct_per_trade']
    return math.floor(max_risk / (width * 100))


def build_bps_legs(underlying, expiration, short_put, long_put, contracts):
    exp = expiration if isinstance(expiration, date) else date.fromisoformat(expiration)
    return [
        {'symbol': build_option_symbol(underlying, exp, 'P', short_put),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'P', long_put),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
    ]


def build_ic_legs(underlying, expiration, short_put, long_put, short_call, long_call, contracts):
    exp = expiration if isinstance(expiration, date) else date.fromisoformat(expiration)
    return [
        {'symbol': build_option_symbol(underlying, exp, 'P', short_put),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'P', long_put),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'C', short_call),
         'quantity': contracts, 'action': 'SELL_TO_OPEN'},
        {'symbol': build_option_symbol(underlying, exp, 'C', long_call),
         'quantity': contracts, 'action': 'BUY_TO_OPEN'},
    ]


def _is_filled(client, order_id):
    order = client.get_order(order_id)
    return str(order.status).lower() in _FILL_STATUSES


def place_spread(client, db_path, setup, scan_id, net_liq):
    underlying = setup['underlying']

    if any(t['underlying'] == underlying for t in get_open_trades(db_path)):
        return None

    contracts = calc_contracts(net_liq, setup['width'])
    if contracts < 1:
        return None

    if setup['strategy'] == 'bull_put_spread':
        legs = build_bps_legs(
            underlying, setup['expiration'],
            setup['short_put_strike'], setup['long_put_strike'],
            contracts,
        )
    else:
        legs = build_ic_legs(
            underlying, setup['expiration'],
            setup['short_put_strike'], setup['long_put_strike'],
            setup['short_call_strike'], setup['long_call_strike'],
            contracts,
        )

    credit = setup['credit']
    response = client.place_order(legs, credit)
    order_id = str(response.order.id)

    trade_id = insert_trade(db_path, {
        'scan_id': scan_id,
        'underlying': underlying,
        'strategy': setup['strategy'],
        'expiration': setup['expiration'],
        'short_put_strike': setup['short_put_strike'],
        'long_put_strike': setup['long_put_strike'],
        'short_call_strike': setup['short_call_strike'],
        'long_call_strike': setup['long_call_strike'],
        'entry_credit': credit,
        'entry_ts': datetime.now(timezone.utc).isoformat(),
        'contracts': contracts,
        'order_id': order_id,
    })
    update_trade_status(db_path, trade_id, 'pending')

    time.sleep(_FILL_WAIT_SECS)
    if _is_filled(client, order_id):
        update_trade_status(db_path, trade_id, 'open')
        mark_scan_traded(db_path, scan_id)
        return trade_id

    # Reprice 10% lower (more aggressive credit) and retry once
    client.cancel_order(order_id)
    natural_credit = round(credit * 0.90, 2)
    response2 = client.place_order(legs, natural_credit)
    order_id2 = str(response2.order.id)
    update_trade_order_id(db_path, trade_id, order_id2)

    time.sleep(_FILL_WAIT_SECS)
    if _is_filled(client, order_id2):
        update_trade_status(db_path, trade_id, 'open')
        mark_scan_traded(db_path, scan_id)
        return trade_id

    client.cancel_order(order_id2)
    update_trade_status(db_path, trade_id, 'cancelled')
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_trader.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot/trader.py tests/test_trader.py
git commit -m "feat: add trader with position sizing and order placement"
```

---

## Task 6: Manager

**Files:**
- Create: `bot/manager.py`
- Create: `tests/test_manager.py`

- [ ] **Step 1: Write failing tests in `tests/test_manager.py`**

```python
from bot.manager import should_close, calc_pnl_pct


def test_profit_target_hit():
    # entry=2.00, mark=1.00 → 50% profit
    assert should_close(entry_credit=2.0, current_mark=1.0) == 'profit_target'


def test_stop_loss_hit():
    # entry=2.00, mark=6.00 → 200% loss
    assert should_close(entry_credit=2.0, current_mark=6.0) == 'stop_loss'


def test_dte_expire():
    assert should_close(entry_credit=2.0, current_mark=1.8, dte=5) == 'dte_expire'


def test_no_close_midway():
    assert should_close(entry_credit=2.0, current_mark=1.5, dte=20) is None


def test_calc_pnl_pct_profit():
    assert calc_pnl_pct(entry_credit=2.0, current_mark=1.0) == 50.0


def test_calc_pnl_pct_loss():
    assert calc_pnl_pct(entry_credit=2.0, current_mark=6.0) == -200.0


def test_profit_beats_dte():
    # both profit target and DTE triggered — profit_target is checked first
    assert should_close(entry_credit=2.0, current_mark=1.0, dte=5) == 'profit_target'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_manager.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.manager'`

- [ ] **Step 3: Implement `bot/manager.py`**

```python
import time
from datetime import date, datetime, timezone
from bot.config import EXIT_RULES
from bot.scanner import build_option_symbol, calc_dte
from bot.db import get_open_trades, update_trade_closed, update_trade_status

_FILL_WAIT_SECS = 120
_FILL_STATUSES = {'filled', 'partially_filled'}


def calc_pnl_pct(entry_credit: float, current_mark: float) -> float:
    return round((entry_credit - current_mark) / entry_credit * 100, 2)


def should_close(entry_credit: float, current_mark: float, dte: int = 99):
    pnl_pct = calc_pnl_pct(entry_credit, current_mark)
    if pnl_pct >= EXIT_RULES['profit_target_pct']:
        return 'profit_target'
    if pnl_pct <= -EXIT_RULES['stop_loss_pct']:
        return 'stop_loss'
    if dte <= EXIT_RULES['dte_close']:
        return 'dte_expire'
    return None


def _get_spread_mark(client, trade):
    positions = client.get_positions()
    pos_map = {str(p.symbol): float(p.mark_price) for p in positions}

    underlying = trade['underlying']
    exp = date.fromisoformat(trade['expiration'])

    def mark(opt_type, strike):
        sym = build_option_symbol(underlying, exp, opt_type, strike)
        return pos_map.get(sym, 0.0)

    cost_to_close = mark('P', trade['long_put_strike']) - mark('P', trade['short_put_strike'])
    if trade['strategy'] == 'iron_condor':
        cost_to_close += (mark('C', trade['long_call_strike'])
                          - mark('C', trade['short_call_strike']))
    return cost_to_close


def _build_close_legs(trade):
    underlying = trade['underlying']
    exp = date.fromisoformat(trade['expiration'])
    n = trade['contracts']
    legs = [
        {'symbol': build_option_symbol(underlying, exp, 'P', trade['short_put_strike']),
         'quantity': n, 'action': 'BUY_TO_CLOSE'},
        {'symbol': build_option_symbol(underlying, exp, 'P', trade['long_put_strike']),
         'quantity': n, 'action': 'SELL_TO_CLOSE'},
    ]
    if trade['strategy'] == 'iron_condor':
        legs += [
            {'symbol': build_option_symbol(underlying, exp, 'C', trade['short_call_strike']),
             'quantity': n, 'action': 'BUY_TO_CLOSE'},
            {'symbol': build_option_symbol(underlying, exp, 'C', trade['long_call_strike']),
             'quantity': n, 'action': 'SELL_TO_CLOSE'},
        ]
    return legs


def manage_positions(client, db_path):
    today = date.today()
    for trade in get_open_trades(db_path):
        if trade['status'] != 'open':
            continue
        dte = calc_dte(date.fromisoformat(trade['expiration']), today)
        current_mark = _get_spread_mark(client, trade)
        reason = should_close(trade['entry_credit'], current_mark, dte)
        if reason is None:
            continue

        legs = _build_close_legs(trade)
        response = client.place_debit_order(legs, round(current_mark, 2))
        order_id = str(response.order.id)
        update_trade_status(db_path, trade['id'], 'pending')

        time.sleep(_FILL_WAIT_SECS)
        order = client.get_order(order_id)
        if str(order.status).lower() in _FILL_STATUSES:
            update_trade_closed(
                db_path, trade['id'],
                close_credit=current_mark,
                close_reason=reason,
                close_ts=datetime.now(timezone.utc).isoformat(),
            )
        else:
            client.cancel_order(order_id)
            update_trade_status(db_path, trade['id'], 'open')
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_manager.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot/manager.py tests/test_manager.py
git commit -m "feat: add position manager with exit rule logic"
```

---

## Task 7: Scheduler Entry Point

**Files:**
- Create: `bot/main.py`

No unit tests for `main.py` — it is a wiring file only.

- [ ] **Step 1: Create `bot/main.py`**

```python
import logging
from datetime import datetime
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import config
from bot.db import init_db, insert_scan, insert_account_snapshot
from bot.tt_client import TastytradeClient
from bot.scanner import scan_underlying
from bot.trader import place_spread
from bot.manager import manage_positions

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger(__name__)

ET = pytz.timezone(config.TIMEZONE)
client = TastytradeClient(
    config.TASTYTRADE_USERNAME,
    config.TASTYTRADE_PASSWORD,
    config.TASTYTRADE_ACCOUNT_NUMBER,
)


def job_scan():
    log.info('Scanner starting')
    try:
        balance = client.get_account_balance()
        net_liq = float(balance.net_liquidating_value)
        for symbol in config.UNDERLYINGS:
            setups = scan_underlying(client, symbol)
            for setup in setups:
                scan_id = insert_scan(config.DB_PATH, {
                    **setup,
                    'ts': datetime.now(ET).isoformat(),
                    'traded': 0,
                })
                log.info('Setup: %s %s exp %s credit $%.2f',
                         setup['strategy'], symbol, setup['expiration'], setup['credit'])
                place_spread(client, config.DB_PATH, setup, scan_id, net_liq)
    except Exception:
        log.exception('Scanner error')


def job_manage():
    log.info('Manager starting')
    try:
        manage_positions(client, config.DB_PATH)
    except Exception:
        log.exception('Manager error')


def job_snapshot():
    log.info('Account snapshot')
    try:
        balance = client.get_account_balance()
        insert_account_snapshot(config.DB_PATH, {
            'ts': datetime.now(ET).isoformat(),
            'net_liq': float(balance.net_liquidating_value),
            'cash': float(balance.cash_balance),
            'open_pnl': float(getattr(balance, 'unrealized_day_profit_loss', 0)),
            'realized_pnl_today': float(getattr(balance, 'realized_day_profit_loss', 0)),
        })
    except Exception:
        log.exception('Snapshot error')


def main():
    init_db(config.DB_PATH)
    client.connect()
    log.info('Connected to Tastytrade — account %s', config.TASTYTRADE_ACCOUNT_NUMBER)

    scheduler = BlockingScheduler(timezone=ET)

    # Scan every 15 min during market hours
    scheduler.add_job(
        job_scan,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='*/15', second=0, timezone=ET),
        id='scanner',
    )
    # Manage positions every 5 min
    scheduler.add_job(
        job_manage,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='*/5', second=30, timezone=ET),
        id='manager',
    )
    # Hourly account snapshot
    scheduler.add_job(
        job_snapshot,
        CronTrigger(day_of_week='mon-fri', hour='10-15', minute=0, second=0, timezone=ET),
        id='snapshot',
    )

    log.info('Scheduler running. Ctrl+C to stop.')
    scheduler.start()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Smoke-test the import (no Tastytrade creds needed)**

```bash
$env:TASTYTRADE_USERNAME = "test"
$env:TASTYTRADE_PASSWORD = "test"
$env:TASTYTRADE_ACCOUNT_NUMBER = "test"
cd "c:\Users\shayn\Desktop\Kalshi-Bot"
python -c "from bot.main import main; print('Import OK')"
```

Expected: `Import OK` with no errors or tracebacks.

- [ ] **Step 3: Commit**

```bash
git add bot/main.py
git commit -m "feat: add APScheduler entry point wiring scan/manage/snapshot jobs"
```

---

## Task 8: Node.js Options Route

**Files:**
- Modify: `web/routes/options.js`

`better-sqlite3` is already in `package.json` — no install needed.

- [ ] **Step 1: Rewrite `web/routes/options.js`**

Replace the entire file with:

```javascript
const router = require('express').Router();
const path = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');

const DB_PATH = path.join(__dirname, '../../bot/data/options.db');

function getDb() {
  try {
    return new Database(DB_PATH, { readonly: true, fileMustExist: true });
  } catch {
    return null;
  }
}

router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  let openTrades = [];
  let closedTrades = [];
  let recentScans = [];
  let accountSnap = null;

  if (db) {
    try {
      openTrades = db.prepare(
        "SELECT * FROM trades WHERE status IN ('open','pending') ORDER BY entry_ts DESC"
      ).all();
      closedTrades = db.prepare(
        "SELECT * FROM trades WHERE status='closed' ORDER BY close_ts DESC LIMIT 50"
      ).all();
      recentScans = db.prepare(
        "SELECT * FROM scans ORDER BY id DESC LIMIT 20"
      ).all();
      accountSnap = db.prepare(
        "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1"
      ).get() || null;
    } finally {
      db.close();
    }
  }

  res.render('options', {
    user: req.session.user,
    openTrades,
    closedTrades,
    recentScans,
    accountSnap,
    botOnline: db !== null,
  });
});

module.exports = router;
```

- [ ] **Step 2: Verify the route loads**

```bash
cd "c:\Users\shayn\Desktop\Kalshi-Bot"
node -e "require('./web/routes/options'); console.log('Route OK')"
```

Expected: `Route OK`

- [ ] **Step 3: Commit**

```bash
git add web/routes/options.js
git commit -m "feat: wire options route to read SQLite for dashboard data"
```

---

## Task 9: Options Dashboard UI

**Files:**
- Modify: `web/views/options.ejs`

- [ ] **Step 1: Replace `web/views/options.ejs` with the full dashboard**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="60">
  <title>Options Trading — Sharp Bot</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/style.css?v=7">
  <style>
    .opt-stat-bar { display: flex; gap: 1.25rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .opt-stat { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.4rem; min-width: 130px; }
    .opt-stat-label { font-size: 0.70rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
    .opt-stat-value { font-family: 'Space Mono', monospace; font-size: 1.2rem; font-weight: 700; }
    .opt-stat-value.pos { color: #00d4a0; }
    .opt-stat-value.neg { color: #ff4f4f; }
    .opt-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .opt-table th { text-align: left; padding: 8px 12px; color: var(--text2); font-weight: 600; font-size: 0.70rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }
    .opt-table td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
    tr.row-profit td { color: #00d4a0; }
    tr.row-loss td { color: #ff4f4f; }
    tr.row-pending td { color: #f5a623; }
    .badge { display:inline-block; font-size:0.63rem; padding:2px 7px; border-radius:4px; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; }
    .b-bps   { background:rgba(100,180,255,0.12); color:#64b4ff; }
    .b-ic    { background:rgba(200,130,255,0.12); color:#c882ff; }
    .b-open  { background:rgba(0,212,160,0.12);  color:#00d4a0; }
    .b-pend  { background:rgba(245,166,35,0.15); color:#f5a623; }
    .b-closed{ background:rgba(255,255,255,0.06);color:var(--text2); }
    .b-traded{ background:rgba(0,212,160,0.15);  color:#00d4a0; }
    .sect-hdr { font-size:0.82rem; font-weight:700; color:var(--accent); margin:0 0 0.75rem; text-transform:uppercase; letter-spacing:0.07em; }
    .offline-bar { background:rgba(255,79,79,0.08); border:1px solid rgba(255,79,79,0.25); border-radius:8px; padding:0.7rem 1rem; font-size:0.82rem; color:#ff4f4f; margin-bottom:1.25rem; }
    details summary { cursor:pointer; color:var(--text2); font-size:0.82rem; padding:4px 0; user-select:none; }
    details summary:hover { color:var(--text); }
    details[open] summary { color:var(--accent); }
  </style>
</head>
<body>
  <div id="vanta-bg"></div>
  <nav class="navbar">
    <div class="nav-brand">⚡ Sharp Bot</div>
    <div class="nav-links">
      <div class="nav-dropdown">
        <button class="nav-dropdown-trigger">Predictions <span class="dd-arrow">▼</span></button>
        <div class="nav-dropdown-menu">
          <a href="/">Dashboard</a>
          <a href="/history">History</a>
          <a href="/spreads">Spreads</a>
          <a href="/portfolio">Portfolio</a>
        </div>
      </div>
      <div class="nav-dropdown">
        <button class="nav-dropdown-trigger active">Options Trading <span class="dd-arrow">▼</span></button>
        <div class="nav-dropdown-menu">
          <a href="/options" class="active">Dashboard</a>
        </div>
      </div>
      <a href="/settings">Settings</a>
      <% if (user.role === 'admin') { %><a href="/admin">Admin</a><% } %>
    </div>
    <div class="nav-right">
      <span class="nav-user">@<%= user.username %></span>
      <a href="/auth/logout" class="btn-logout">Logout</a>
    </div>
  </nav>

  <div class="container" style="padding-top:2rem;">

    <% if (!botOnline) { %>
      <div class="offline-bar">Bot offline — start the Python bot to populate data.</div>
    <% } %>

    <%
    function money(n) {
      if (n == null) return '—';
      const v = parseFloat(n);
      return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
    }
    function pclass(n) { return n == null ? '' : parseFloat(n) >= 0 ? 'pos' : 'neg'; }
    function stratBadge(s) {
      return s === 'bull_put_spread'
        ? '<span class="badge b-bps">BPS</span>'
        : '<span class="badge b-ic">IC</span>';
    }
    function strikes(t) {
      let s = t.short_put_strike + '/' + t.long_put_strike;
      if (t.short_call_strike) s += ' · ' + t.short_call_strike + '/' + t.long_call_strike;
      return s;
    }
    function dte(expStr) {
      return Math.max(0, Math.round((new Date(expStr) - new Date()) / 86400000));
    }
    %>

    <!-- Account Summary -->
    <div class="opt-stat-bar">
      <div class="opt-stat">
        <div class="opt-stat-label">Net Liq</div>
        <div class="opt-stat-value"><%= accountSnap ? money(accountSnap.net_liq) : '—' %></div>
      </div>
      <div class="opt-stat">
        <div class="opt-stat-label">Cash</div>
        <div class="opt-stat-value"><%= accountSnap ? money(accountSnap.cash) : '—' %></div>
      </div>
      <div class="opt-stat">
        <div class="opt-stat-label">Open P&amp;L</div>
        <div class="opt-stat-value <%= accountSnap ? pclass(accountSnap.open_pnl) : '' %>">
          <%= accountSnap ? money(accountSnap.open_pnl) : '—' %>
        </div>
      </div>
      <div class="opt-stat">
        <div class="opt-stat-label">Realized Today</div>
        <div class="opt-stat-value <%= accountSnap ? pclass(accountSnap.realized_pnl_today) : '' %>">
          <%= accountSnap ? money(accountSnap.realized_pnl_today) : '—' %>
        </div>
      </div>
    </div>

    <!-- Open Positions -->
    <div class="card" style="padding:1.25rem 1.5rem;">
      <div class="sect-hdr">Open Positions</div>
      <% if (!openTrades.length) { %>
        <p style="color:var(--text2);font-size:0.85rem;margin:0.25rem 0 0;">No open positions.</p>
      <% } else { %>
      <div style="overflow-x:auto;">
        <table class="opt-table">
          <thead><tr>
            <th>Symbol</th><th>Strategy</th><th>Strikes</th><th>Expiration</th>
            <th>DTE</th><th>Entry Credit</th><th>Status</th>
          </tr></thead>
          <tbody>
          <% openTrades.forEach(t => { %>
            <tr class="<%= t.status === 'pending' ? 'row-pending' : '' %>">
              <td><strong><%= t.underlying %></strong></td>
              <td><%- stratBadge(t.strategy) %></td>
              <td style="font-family:'Space Mono',monospace;font-size:0.78rem;"><%= strikes(t) %></td>
              <td><%= t.expiration %></td>
              <td><%= dte(t.expiration) %></td>
              <td style="font-family:'Space Mono',monospace;">$<%= parseFloat(t.entry_credit).toFixed(2) %></td>
              <td><span class="badge <%= t.status === 'pending' ? 'b-pend' : 'b-open' %>"><%= t.status %></span></td>
            </tr>
          <% }); %>
          </tbody>
        </table>
      </div>
      <% } %>
    </div>

    <!-- Scanner Feed -->
    <div class="card" style="padding:1.25rem 1.5rem;margin-top:1.5rem;">
      <div class="sect-hdr">Scanner Feed</div>
      <% if (!recentScans.length) { %>
        <p style="color:var(--text2);font-size:0.85rem;margin:0.25rem 0 0;">No scans yet.</p>
      <% } else { %>
      <div style="overflow-x:auto;">
        <table class="opt-table">
          <thead><tr>
            <th>Time</th><th>Symbol</th><th>Strategy</th><th>Strikes</th>
            <th>Credit</th><th>IV Rank</th><th>Delta</th><th>DTE</th><th></th>
          </tr></thead>
          <tbody>
          <% recentScans.forEach(s => { %>
            <tr>
              <td style="color:var(--text2);font-size:0.74rem;white-space:nowrap;"><%= s.ts ? s.ts.slice(11,16) : '—' %></td>
              <td><strong><%= s.underlying %></strong></td>
              <td><%- stratBadge(s.strategy) %></td>
              <td style="font-family:'Space Mono',monospace;font-size:0.78rem;"><%= strikes(s) %></td>
              <td style="font-family:'Space Mono',monospace;">$<%= parseFloat(s.credit).toFixed(2) %></td>
              <td><%= s.iv_rank != null ? parseFloat(s.iv_rank).toFixed(0) : '—' %></td>
              <td><%= s.delta != null ? parseFloat(s.delta).toFixed(2) : '—' %></td>
              <td><%= s.dte %></td>
              <td><% if (s.traded) { %><span class="badge b-traded">Traded</span><% } %></td>
            </tr>
          <% }); %>
          </tbody>
        </table>
      </div>
      <% } %>
    </div>

    <!-- Trade History -->
    <div class="card" style="padding:1.25rem 1.5rem;margin-top:1.5rem;margin-bottom:2rem;">
      <details>
        <summary>
          <strong style="color:var(--text);">Trade History</strong>
          <span style="color:var(--text2);margin-left:6px;">(<%= closedTrades.length %> closed)</span>
        </summary>
        <% if (!closedTrades.length) { %>
          <p style="color:var(--text2);font-size:0.85rem;margin:0.75rem 0 0;">No closed trades yet.</p>
        <% } else { %>
        <div style="overflow-x:auto;margin-top:1rem;">
          <table class="opt-table">
            <thead><tr>
              <th>Symbol</th><th>Strategy</th><th>Strikes</th><th>Expiration</th>
              <th>Entry</th><th>Close</th><th>P&amp;L</th><th>Reason</th><th>Date</th>
            </tr></thead>
            <tbody>
            <% closedTrades.forEach(t => {
              const entry = parseFloat(t.entry_credit);
              const close = t.close_credit != null ? parseFloat(t.close_credit) : null;
              const pnl   = close != null ? (entry - close) * t.contracts * 100 : null;
              const rclass = pnl == null ? '' : pnl >= 0 ? 'row-profit' : 'row-loss';
            %>
              <tr class="<%= rclass %>">
                <td><strong><%= t.underlying %></strong></td>
                <td><%- stratBadge(t.strategy) %></td>
                <td style="font-family:'Space Mono',monospace;font-size:0.78rem;"><%= strikes(t) %></td>
                <td><%= t.expiration %></td>
                <td style="font-family:'Space Mono',monospace;">$<%= entry.toFixed(2) %></td>
                <td style="font-family:'Space Mono',monospace;"><%= close != null ? '$'+close.toFixed(2) : '—' %></td>
                <td style="font-family:'Space Mono',monospace;font-weight:700;">
                  <%= pnl != null ? (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) : '—' %>
                </td>
                <td style="font-size:0.78rem;"><%= t.close_reason ? t.close_reason.replace(/_/g,' ') : '—' %></td>
                <td style="color:var(--text2);font-size:0.74rem;"><%= t.close_ts ? t.close_ts.slice(0,10) : '—' %></td>
              </tr>
            <% }); %>
            </tbody>
          </table>
        </div>
        <% } %>
      </details>
    </div>

  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r134/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/vanta@latest/dist/vanta.net.min.js"></script>
  <script>
    VANTA.NET({
      el: '#vanta-bg', mouseControls: true, touchControls: true, gyroControls: false,
      minHeight: 200.00, minWidth: 200.00, scale: 1.00, scaleMobile: 1.00,
      color: 0x00d4a0, backgroundColor: 0x020509,
      points: 16.0, maxDistance: 22.0, spacing: 12.0, opacity: 0.30
    });
  </script>
  <script src="/nav.js?v=4"></script>
</body>
</html>
```

- [ ] **Step 2: Start the server and verify the dashboard renders**

```bash
node web/server.js
```

Navigate to `http://localhost:3000/options`. Expected: Dashboard renders with offline notice, all four panels visible with empty states. No console errors.

- [ ] **Step 3: Commit**

```bash
git add web/views/options.ejs
git commit -m "feat: build Options Trading dashboard UI"
```

---

## Task 10: Nav Dropdown — All Other Pages

**Files:**
- Modify: `web/views/dashboard.ejs`, `web/views/spreads.ejs`, `web/views/history.ejs`, `web/views/portfolio.ejs`, `web/views/settings.ejs`, `web/views/admin.ejs`

In each of the six files, find the standalone Options Trading link. It will be one of:

```html
<a href="/options" class="nav-options-link active">Options Trading</a>
```
or
```html
<a href="/options" class="nav-options-link">Options Trading</a>
```

Replace it with:

```html
<div class="nav-dropdown">
  <button class="nav-dropdown-trigger">Options Trading <span class="dd-arrow">▼</span></button>
  <div class="nav-dropdown-menu">
    <a href="/options">Dashboard</a>
  </div>
</div>
```

- [ ] **Step 1: Update `web/views/dashboard.ejs`** — find and replace the standalone Options link

- [ ] **Step 2: Update `web/views/spreads.ejs`** — same replacement

- [ ] **Step 3: Update `web/views/history.ejs`** — same replacement

- [ ] **Step 4: Update `web/views/portfolio.ejs`** — same replacement

- [ ] **Step 5: Update `web/views/settings.ejs`** — same replacement

- [ ] **Step 6: Update `web/views/admin.ejs`** — same replacement

- [ ] **Step 7: Verify all nav bars work**

Start the server and visit Dashboard, Spreads, History, Portfolio, Settings. Each page should show "Options Trading ▼" dropdown that opens to "Dashboard".

- [ ] **Step 8: Commit**

```bash
git add web/views/
git commit -m "feat: replace standalone Options link with dropdown across all nav bars"
```

---

## Self-Review

**Spec coverage:**
- ✅ SQLite schema: `scans`, `trades`, `account_snapshots` (Task 1)
- ✅ Config: tickers, entry rules, exit rules, position sizing, credentials from env (Task 2)
- ✅ Tastytrade API client: auth, chain, account, orders (Task 3)
- ✅ Scanner: IV rank ≥ 30, delta ≤ 0.30, DTE 21–45, credit ≥ 1/3 width (Task 4)
- ✅ Bull Put Spread finder (Task 4)
- ✅ Iron Condor finder (Task 4)
- ✅ Position sizing 5% of net liq, skip if < 1 contract (Task 5)
- ✅ Order placement with mid → natural retry, cancel on second miss (Task 5)
- ✅ Skip if open position already on underlying (Task 5)
- ✅ 50% profit target / 200% stop loss / DTE ≤ 7 close (Task 6)
- ✅ APScheduler: scan every 15 min, manage every 5 min, snapshot hourly (Task 7)
- ✅ Market hours: Mon–Fri 9:45–15:45 ET (Task 7)
- ✅ Node.js route reads SQLite, handles offline gracefully (Task 8)
- ✅ Account summary bar (Task 9)
- ✅ Open positions table with status color coding (Task 9)
- ✅ Scanner feed with Traded badge (Task 9)
- ✅ Trade history collapsible with P&L calculation (Task 9)
- ✅ 60-second meta-refresh (Task 9)
- ✅ "Bot offline" notice when DB not found (Task 9)
- ✅ Options Trading nav dropdown on all pages (Tasks 9, 10)

**Placeholder scan:** None found.

**Type consistency:**
- `build_option_symbol(underlying, exp, opt_type, strike)` — defined in `scanner.py`, imported in `trader.py` and `manager.py` ✅
- `calc_dte(expiration, today)` — defined in `scanner.py`, imported in `manager.py` ✅
- `get_open_trades`, `insert_scan`, `insert_trade`, `update_trade_closed`, `update_trade_status`, `update_trade_order_id`, `mark_scan_traded`, `insert_account_snapshot` — all defined in `db.py`, used consistently across `trader.py`, `manager.py`, `main.py` ✅
- `TastytradeClient.place_order(legs, price_credit)` and `place_debit_order(legs, price_debit)` — defined in `tt_client.py`, called with same signature in `trader.py` and `manager.py` ✅
