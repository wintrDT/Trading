# Tradovate Futures Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated futures trading bot on Tradovate with VWAP reversion and Opening Range Breakout strategies, risk management, and a live dashboard on the Sharp Bot website.

**Architecture:** Python bot in `bot/futures/` (mirroring the options bot structure) connects to Tradovate's REST + WebSocket APIs, runs two strategies with shared risk management, writes trade data to `bot/data/futures.db`, and a Node.js web route reads that DB to power the `/futures` dashboard.

**Tech Stack:** Python 3.11+, `websockets`, `httpx`, `apscheduler`, `pytz`, `better-sqlite3` (Node), Express/EJS (web dashboard), Alpha Vantage News API (free tier)

---

## File Structure

**New files:**
- `bot/futures/__init__.py` — empty package marker
- `bot/futures/config.py` — futures-specific config (symbols, strategy params, risk rules, env vars)
- `bot/futures/db.py` — SQLite helpers for futures.db (init, insert, query)
- `bot/futures/tradovate_client.py` — Tradovate REST auth + order placement + WebSocket market data
- `bot/futures/strategy.py` — VWAP reversion and Opening Range Breakout signal generation
- `bot/futures/risk.py` — per-trade stop calculation, daily loss limit check, news blackout check
- `bot/futures/trader.py` — place and close futures orders via client
- `bot/futures/manager.py` — monitor open positions, trigger exits
- `bot/futures/main.py` — APScheduler orchestration, entry point
- `bot/futures/news.py` — fetch economic calendar blackout windows + live news headlines from Alpha Vantage
- `web/routes/futures.js` — replace placeholder with real DB-reading route
- `web/views/futures.ejs` — full futures dashboard (positions, signals, history, account stats, news feed)
- `tests/futures/test_db.py`
- `tests/futures/test_tradovate_client.py`
- `tests/futures/test_strategy.py`
- `tests/futures/test_risk.py`
- `tests/futures/test_futures_trader.py`
- `tests/futures/test_futures_manager.py`

**Modified files:**
- `.env` — add `TV_USERNAME`, `TV_PASSWORD`, `TV_CID`, `TV_SEC`, `TV_DEMO=true`

---

### Task 1: Futures config and DB

**Files:**
- Create: `bot/futures/__init__.py`
- Create: `bot/futures/config.py`
- Create: `bot/futures/db.py`
- Test: `tests/futures/test_db.py`

- [ ] **Step 1: Create package init**

```python
# bot/futures/__init__.py
# (empty)
```

- [ ] **Step 2: Write failing test**

Create `tests/futures/__init__.py` (empty) and `tests/futures/test_db.py`:

```python
import os, tempfile, pytest
from bot.futures.db import init_db, insert_signal, insert_trade, get_open_trades, insert_snapshot

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_init_creates_tables(tmp_db):
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert 'futures_trades' in tables
    assert 'futures_signals' in tables
    assert 'futures_snapshots' in tables
    assert 'futures_settings' in tables

def test_insert_and_get_trade(tmp_db):
    trade = {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.25, 'entry_ts': '2026-01-01T10:00:00',
        'contracts': 1, 'stop_price': 4998.25, 'target_price': 5004.25,
        'order_id': 'TEST123', 'status': 'open',
    }
    trade_id = insert_trade(tmp_db, trade)
    assert trade_id > 0
    open_trades = get_open_trades(tmp_db)
    assert len(open_trades) == 1
    assert open_trades[0]['symbol'] == 'ES'
```

- [ ] **Step 3: Run test to verify it fails**

```
cd c:\Users\shayn\Desktop\Kalshi-Bot
python -m pytest tests/futures/test_db.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'bot.futures'`

- [ ] **Step 4: Create config.py**

```python
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
TV_DEMO      = os.environ.get('TV_DEMO', 'true').lower() == 'true'

BASE_URL = 'https://demo.tradovateapi.com/v1' if TV_DEMO else 'https://live.tradovateapi.com/v1'
WS_URL   = 'wss://md.tradovateapi.com/v1/websocket'
```

- [ ] **Step 5: Create db.py**

```python
# bot/futures/db.py
import contextlib
import sqlite3

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS futures_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    strategy TEXT,
    direction TEXT,
    entry_price REAL,
    entry_ts TEXT,
    current_price REAL,
    close_price REAL,
    close_ts TEXT,
    close_reason TEXT,
    stop_price REAL,
    target_price REAL,
    contracts INTEGER DEFAULT 1,
    order_id TEXT,
    status TEXT DEFAULT 'open',
    pnl REAL
)
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS futures_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    symbol TEXT,
    strategy TEXT,
    direction TEXT,
    price REAL,
    vwap REAL,
    orb_high REAL,
    orb_low REAL,
    traded INTEGER DEFAULT 0
)
"""

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS futures_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    net_liq REAL,
    cash REAL,
    open_pnl REAL,
    realized_pnl_today REAL
)
"""

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS futures_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""


@contextlib.contextmanager
def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def init_db(db_path):
    with _conn(db_path) as conn:
        conn.execute(_CREATE_TRADES)
        conn.execute(_CREATE_SIGNALS)
        conn.execute(_CREATE_SNAPSHOTS)
        conn.execute(_CREATE_SETTINGS)


def get_setting(db_path, key, default=None):
    with _conn(db_path) as conn:
        row = conn.execute("SELECT value FROM futures_settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def set_setting(db_path, key, value):
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO futures_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def insert_signal(db_path, signal):
    sql = """
    INSERT INTO futures_signals (ts, symbol, strategy, direction, price, vwap, orb_high, orb_low, traded)
    VALUES (:ts, :symbol, :strategy, :direction, :price, :vwap, :orb_high, :orb_low, :traded)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, {'vwap': None, 'orb_high': None, 'orb_low': None, **signal}).lastrowid


def insert_trade(db_path, trade):
    sql = """
    INSERT INTO futures_trades (symbol, strategy, direction, entry_price, entry_ts,
        stop_price, target_price, contracts, order_id, status)
    VALUES (:symbol, :strategy, :direction, :entry_price, :entry_ts,
        :stop_price, :target_price, :contracts, :order_id, :status)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, trade).lastrowid


def update_trade_price(db_path, trade_id, current_price):
    with _conn(db_path) as conn:
        conn.execute("UPDATE futures_trades SET current_price=? WHERE id=?", (current_price, trade_id))


def update_trade_closed(db_path, trade_id, close_price, close_reason, close_ts, pnl):
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE futures_trades SET close_price=?,close_ts=?,close_reason=?,status='closed',pnl=? WHERE id=?",
            (close_price, close_ts, close_reason, pnl, trade_id),
        )


def mark_signal_traded(db_path, signal_id):
    with _conn(db_path) as conn:
        conn.execute("UPDATE futures_signals SET traded=1 WHERE id=?", (signal_id,))


def get_open_trades(db_path):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM futures_trades WHERE status='open' ORDER BY entry_ts DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_signals(db_path, limit=20):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM futures_signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_closed_trades(db_path, limit=50):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM futures_trades WHERE status='closed' ORDER BY close_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def insert_snapshot(db_path, snap):
    sql = """
    INSERT INTO futures_snapshots (ts, net_liq, cash, open_pnl, realized_pnl_today)
    VALUES (:ts, :net_liq, :cash, :open_pnl, :realized_pnl_today)
    """
    with _conn(db_path) as conn:
        conn.execute(sql, snap)


def get_daily_pnl(db_path, date_str):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed' AND close_ts LIKE ?",
            (f"{date_str}%",)
        ).fetchone()
        return float(row['total'])


def get_all_time_pnl(db_path):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed'"
        ).fetchone()
        return float(row['total'])
```

- [ ] **Step 6: Run tests**

```
python -m pytest tests/futures/test_db.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add bot/futures/__init__.py bot/futures/config.py bot/futures/db.py tests/futures/__init__.py tests/futures/test_db.py
git commit -m "feat: futures bot config and DB layer"
```

---

### Task 2: Tradovate client — auth and REST

**Files:**
- Create: `bot/futures/tradovate_client.py`
- Test: `tests/futures/test_tradovate_client.py`

- [ ] **Step 1: Write failing test**

```python
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
    with patch('httpx.post', return_value=mock_response):
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
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_tradovate_client.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement tradovate_client.py**

```python
# bot/futures/tradovate_client.py
import json
import asyncio
import logging
import httpx
import websockets
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _parse_quote_frame(frame: str) -> list:
    """Parse a Tradovate WebSocket market data frame into quote dicts."""
    if not frame.startswith('a['):
        return []
    try:
        messages = json.loads(frame[1:])
        quotes = []
        for msg in messages:
            if msg.get('e') == 'md' and 'quotes' in msg.get('d', {}):
                quotes.extend(msg['d']['quotes'])
        return quotes
    except (json.JSONDecodeError, KeyError):
        return []


class TradovateClient:
    def __init__(self, username, password, cid, sec, demo=True, device_id='sharp-bot-001'):
        self._username   = username
        self._password   = password
        self._cid        = cid
        self._sec        = sec
        self._demo       = demo
        self._device_id  = device_id
        self.access_token    = None
        self.md_access_token = None
        self._token_expires  = None
        self._account_id     = None
        self._account_spec   = None
        self._base = 'https://demo.tradovateapi.com/v1' if demo else 'https://live.tradovateapi.com/v1'

    # ── Auth ──────────────────────────────────────────────────────────

    def connect(self):
        payload = {
            'name':       self._username,
            'password':   self._password,
            'deviceId':   self._device_id,
            'appId':      'SharpBot',
            'appVersion': '1.0',
            'cid':        int(self._cid) if self._cid else 0,
            'sec':        self._sec,
        }
        resp = httpx.post(f'{self._base}/auth/accesstokenrequest', json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if 'errorText' in data:
            raise RuntimeError(f"Tradovate auth failed: {data['errorText']}")
        self.access_token    = data['accessToken']
        self.md_access_token = data.get('mdAccessToken', self.access_token)
        self._token_expires  = data.get('expirationTime')
        self._fetch_account()
        log.info('Tradovate connected [%s]', 'DEMO' if self._demo else 'LIVE')

    def _refresh_if_needed(self):
        if self._token_expires is None:
            return
        exp = datetime.fromisoformat(self._token_expires.replace('Z', '+00:00'))
        if (exp - datetime.now(timezone.utc)).total_seconds() < 300:
            resp = httpx.post(
                f'{self._base}/auth/renewaccesstoken',
                headers=self._auth_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token    = data['accessToken']
            self.md_access_token = data.get('mdAccessToken', self.access_token)
            self._token_expires  = data.get('expirationTime')
            log.info('Tradovate token refreshed')

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def _fetch_account(self):
        data = self._get('/account/list')
        if not data:
            raise RuntimeError('No Tradovate accounts found')
        acct = data[0]
        self._account_id   = acct['id']
        self._account_spec = acct['name']

    # ── REST helpers ──────────────────────────────────────────────────

    def _get(self, path, params=None):
        self._refresh_if_needed()
        resp = httpx.get(f'{self._base}{path}', params=params, headers=self._auth_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, json=None):
        self._refresh_if_needed()
        resp = httpx.post(f'{self._base}{path}', json=json, headers=self._auth_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Account ───────────────────────────────────────────────────────

    def get_account_balance(self):
        return self._get('/cashBalance/getcashbalancesnapshot', params={'accountId': self._account_id})

    def get_positions(self):
        return self._get('/position/list')

    # ── Orders ────────────────────────────────────────────────────────

    def place_order(self, symbol, action, qty, order_type='Market', price=None, stop_price=None):
        payload = {
            'accountSpec': self._account_spec,
            'accountId':   self._account_id,
            'action':      action,
            'symbol':      symbol,
            'orderQty':    qty,
            'orderType':   order_type,
            'isAutomated': True,
        }
        if price is not None:
            payload['price'] = price
        if stop_price is not None:
            payload['stopPrice'] = stop_price
        return self._post('/order/placeorder', json=payload)

    def cancel_order(self, order_id):
        return self._post('/order/cancelorder', json={'orderId': order_id})

    def get_order(self, order_id):
        return self._get('/order/item', params={'id': order_id})

    # ── WebSocket market data ─────────────────────────────────────────

    async def _stream_quotes_async(self, symbols: list, on_quote, timeout=30):
        uri = 'wss://md.tradovateapi.com/v1/websocket'
        async with websockets.connect(uri, ping_interval=20) as ws:
            opening = await asyncio.wait_for(ws.recv(), timeout=10)
            if opening != 'o':
                raise RuntimeError(f'Unexpected WS open frame: {opening}')
            await ws.send(f'authorize\n0\n\n{self.md_access_token}')
            auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
            auth_data = json.loads(auth_resp[1:])
            if auth_data[0].get('s') != 200:
                raise RuntimeError(f'WS auth failed: {auth_data}')
            for i, sym in enumerate(symbols, start=1):
                await ws.send(f'md/subscribequote\n{i}\n\n{json.dumps({"symbol": sym})}')
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                for quote in _parse_quote_frame(msg):
                    on_quote(quote)

    def get_current_prices(self, symbols: list, timeout=30) -> dict:
        """Returns {symbol: mid_price} for the given continuous symbols (e.g. 'ES', 'NQ')."""
        prices = {}

        def on_quote(q):
            sym  = q.get('symbol', '')
            base = ''.join(c for c in sym if c.isalpha())
            bid  = q.get('bid')
            ask  = q.get('ask')
            if bid and ask:
                prices[base] = round((float(bid) + float(ask)) / 2, 4)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._stream_quotes_async(symbols, on_quote, timeout=timeout))
        finally:
            loop.close()
        return prices
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/futures/test_tradovate_client.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/futures/tradovate_client.py tests/futures/test_tradovate_client.py
git commit -m "feat: Tradovate REST + WebSocket client"
```

---

### Task 3: Strategy engine — VWAP reversion + ORB

**Files:**
- Create: `bot/futures/strategy.py`
- Test: `tests/futures/test_strategy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/futures/test_strategy.py
import pytest
from bot.futures.strategy import VWAPState, calc_vwap, ORBState, check_vwap_signal, check_orb_signal

def test_vwap_calculation():
    state = VWAPState()
    state.add_bar(price=100.0, volume=1000)
    state.add_bar(price=102.0, volume=2000)
    vwap = calc_vwap(state)
    # (100*1000 + 102*2000) / 3000 = 101.333...
    assert abs(vwap - 101.333) < 0.001

def test_vwap_signal_long():
    signal = check_vwap_signal(current_price=99.79, vwap=100.0, deviation_pct=0.15)
    assert signal == 'long'

def test_vwap_signal_short():
    signal = check_vwap_signal(current_price=100.21, vwap=100.0, deviation_pct=0.15)
    assert signal == 'short'

def test_vwap_no_signal():
    signal = check_vwap_signal(current_price=100.05, vwap=100.0, deviation_pct=0.15)
    assert signal is None

def test_orb_not_ready_before_end():
    state = ORBState()
    state.update(price=5000.0, ts_minute=9*60+30)
    assert not state.is_ready(orb_end_minute=10*60)

def test_orb_breakout_long():
    state = ORBState()
    state._high  = 5002.0
    state._low   = 5000.0
    state._ready = True
    signal = check_orb_signal(current_price=5003.0, orb_state=state,
                               orb_end_minute=10*60, min_range_ticks=4, tick=0.25)
    assert signal == 'long'

def test_orb_breakout_short():
    state = ORBState()
    state._high  = 5002.0
    state._low   = 5000.0
    state._ready = True
    signal = check_orb_signal(current_price=4999.0, orb_state=state,
                               orb_end_minute=10*60, min_range_ticks=4, tick=0.25)
    assert signal == 'short'
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_strategy.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement strategy.py**

```python
# bot/futures/strategy.py
from dataclasses import dataclass


@dataclass
class VWAPState:
    _cum_pv: float = 0.0
    _cum_v:  float = 0.0

    def add_bar(self, price: float, volume: float):
        self._cum_pv += price * volume
        self._cum_v  += volume

    def reset(self):
        self._cum_pv = 0.0
        self._cum_v  = 0.0


def calc_vwap(state: VWAPState) -> float | None:
    if state._cum_v == 0:
        return None
    return state._cum_pv / state._cum_v


def check_vwap_signal(current_price: float, vwap: float, deviation_pct: float) -> str | None:
    if vwap == 0:
        return None
    dev = (current_price - vwap) / vwap * 100
    if dev <= -deviation_pct:
        return 'long'
    if dev >= deviation_pct:
        return 'short'
    return None


@dataclass
class ORBState:
    _high:  float = float('-inf')
    _low:   float = float('inf')
    _ready: bool  = False

    def update(self, price: float, ts_minute: int):
        self._high = max(self._high, price)
        self._low  = min(self._low,  price)

    def is_ready(self, orb_end_minute: int) -> bool:
        return self._ready

    def set_ready(self):
        self._ready = True

    @property
    def high(self) -> float:
        return self._high

    @property
    def low(self) -> float:
        return self._low


def check_orb_signal(current_price: float, orb_state: ORBState,
                     orb_end_minute: int, min_range_ticks: int, tick: float) -> str | None:
    if not orb_state._ready:
        return None
    orb_range_ticks = round((orb_state.high - orb_state.low) / tick)
    if orb_range_ticks < min_range_ticks:
        return None
    if current_price > orb_state.high:
        return 'long'
    if current_price < orb_state.low:
        return 'short'
    return None
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/futures/test_strategy.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/futures/strategy.py tests/futures/test_strategy.py
git commit -m "feat: futures strategy engine (VWAP reversion + ORB)"
```

---

### Task 4: Risk manager

**Files:**
- Create: `bot/futures/risk.py`
- Test: `tests/futures/test_risk.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/futures/test_risk.py
import pytest
from bot.futures.risk import calc_stop_price, calc_target_price, calc_pnl, is_daily_loss_limit_hit, is_news_blackout, should_exit

def test_stop_price_long():
    stop = calc_stop_price('long', entry=5000.25, stop_ticks=8, tick=0.25)
    assert stop == 4998.25

def test_stop_price_short():
    stop = calc_stop_price('short', entry=5000.25, stop_ticks=8, tick=0.25)
    assert stop == 5002.25

def test_target_price_long():
    target = calc_target_price('long', entry=5000.25, target_ticks=16, tick=0.25)
    assert target == 5004.25

def test_target_price_short():
    target = calc_target_price('short', entry=5000.25, target_ticks=16, tick=0.25)
    assert target == 4996.25

def test_pnl_long_win():
    pnl = calc_pnl('long', entry=5000.0, close=5004.0, contracts=1, point_value=50.0)
    assert pnl == 200.0

def test_pnl_short_loss():
    pnl = calc_pnl('short', entry=5000.0, close=5004.0, contracts=1, point_value=50.0)
    assert pnl == -200.0

def test_daily_loss_limit_hit():
    assert is_daily_loss_limit_hit(realized=-600.0, limit=500.0) is True
    assert is_daily_loss_limit_hit(realized=-400.0, limit=500.0) is False

def test_news_blackout():
    assert is_news_blackout('2026-01-15T08:30:00', blackout_minutes=5,
                             news_times=['2026-01-15T08:30:00']) is True
    assert is_news_blackout('2026-01-15T08:20:00', blackout_minutes=5,
                             news_times=['2026-01-15T08:30:00']) is False

def test_should_exit_stop_loss_long():
    assert should_exit('long', current_price=4997.0, stop_price=4998.0, target_price=5004.0) == 'stop_loss'

def test_should_exit_profit_target_long():
    assert should_exit('long', current_price=5005.0, stop_price=4998.0, target_price=5004.0) == 'profit_target'

def test_should_exit_none():
    assert should_exit('long', current_price=5001.0, stop_price=4998.0, target_price=5004.0) is None
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_risk.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement risk.py**

```python
# bot/futures/risk.py
from datetime import datetime, timezone


def calc_stop_price(direction: str, entry: float, stop_ticks: int, tick: float) -> float:
    offset = round(stop_ticks * tick, 4)
    return round(entry - offset if direction == 'long' else entry + offset, 4)


def calc_target_price(direction: str, entry: float, target_ticks: int, tick: float) -> float:
    offset = round(target_ticks * tick, 4)
    return round(entry + offset if direction == 'long' else entry - offset, 4)


def calc_pnl(direction: str, entry: float, close: float, contracts: int, point_value: float) -> float:
    points = close - entry if direction == 'long' else entry - close
    return round(points * contracts * point_value, 2)


def is_daily_loss_limit_hit(realized: float, limit: float) -> bool:
    return realized <= -abs(limit)


def is_news_blackout(now_iso: str, blackout_minutes: int, news_times: list) -> bool:
    now_dt = datetime.fromisoformat(now_iso.replace('Z', '+00:00'))
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    for nt in news_times:
        event_dt = datetime.fromisoformat(nt.replace('Z', '+00:00'))
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        if abs((now_dt - event_dt).total_seconds()) / 60 <= blackout_minutes:
            return True
    return False


def should_exit(direction: str, current_price: float, stop_price: float, target_price: float) -> str | None:
    if direction == 'long':
        if current_price <= stop_price:
            return 'stop_loss'
        if current_price >= target_price:
            return 'profit_target'
    else:
        if current_price >= stop_price:
            return 'stop_loss'
        if current_price <= target_price:
            return 'profit_target'
    return None
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/futures/test_risk.py -v
```

Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/futures/risk.py tests/futures/test_risk.py
git commit -m "feat: futures risk manager (stops, targets, daily loss limit, news blackout)"
```

---

### Task 5: Futures trader (order placement)

**Files:**
- Create: `bot/futures/trader.py`
- Test: `tests/futures/test_futures_trader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/futures/test_futures_trader.py
import os, tempfile, pytest
from unittest.mock import MagicMock
from bot.futures.db import init_db, get_open_trades
from bot.futures.trader import place_entry, close_trade

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_place_entry_sim_inserts_trade(tmp_db):
    client = MagicMock()
    signal = {'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long', 'price': 5000.25, 'signal_id': 1}
    trade_id = place_entry(client, tmp_db, signal, contracts=1, sim=True)
    assert trade_id is not None
    trades = get_open_trades(tmp_db)
    assert len(trades) == 1
    assert trades[0]['order_id'] == 'SIM'
    assert trades[0]['stop_price'] < 5000.25

def test_place_entry_live_calls_api(tmp_db):
    client = MagicMock()
    client.place_order.return_value = {'orderId': 99, 'orderStatus': 'Filled'}
    signal = {'symbol': 'ES', 'strategy': 'orb', 'direction': 'short', 'price': 5010.0, 'signal_id': 1}
    trade_id = place_entry(client, tmp_db, signal, contracts=1, sim=False)
    client.place_order.assert_called_once()
    assert trade_id is not None

def test_no_duplicate_entry(tmp_db):
    client = MagicMock()
    client.place_order.return_value = {'orderId': 1}
    signal = {'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long', 'price': 5000.0, 'signal_id': 1}
    place_entry(client, tmp_db, signal, contracts=1, sim=True)
    result = place_entry(client, tmp_db, signal, contracts=1, sim=True)
    assert result is None
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_futures_trader.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement trader.py**

```python
# bot/futures/trader.py
import logging
from datetime import datetime, timezone
from bot.futures.config import TICK_INFO, RISK_RULES
from bot.futures.risk import calc_stop_price, calc_target_price, calc_pnl
from bot.futures.db import insert_trade, update_trade_closed, mark_signal_traded, get_open_trades

log = logging.getLogger(__name__)


def place_entry(client, db_path, signal, contracts, sim=False):
    symbol    = signal['symbol']
    direction = signal['direction']
    price     = float(signal['price'])
    signal_id = signal.get('signal_id')

    if any(t['symbol'] == symbol for t in get_open_trades(db_path)):
        log.info('Skipping %s %s — already have open trade', symbol, direction)
        return None

    tick_data    = TICK_INFO.get(symbol, TICK_INFO['ES'])
    tick         = tick_data['tick']
    stop_price   = calc_stop_price(direction, price, RISK_RULES['stop_ticks'], tick)
    target_price = calc_target_price(direction, price, RISK_RULES['target_ticks'], tick)

    order_id = 'SIM'
    if not sim:
        action   = 'Buy' if direction == 'long' else 'Sell'
        resp     = client.place_order(symbol, action, contracts)
        order_id = str(resp.get('orderId', 'UNKNOWN'))

    trade_id = insert_trade(db_path, {
        'symbol':       symbol,
        'strategy':     signal['strategy'],
        'direction':    direction,
        'entry_price':  price,
        'entry_ts':     datetime.now(timezone.utc).isoformat(),
        'stop_price':   stop_price,
        'target_price': target_price,
        'contracts':    contracts,
        'order_id':     order_id,
        'status':       'open',
    })
    if signal_id:
        mark_signal_traded(db_path, signal_id)
    log.info('Entry: %s %s %s @ %.2f stop=%.2f target=%.2f%s',
             direction, symbol, signal['strategy'], price, stop_price, target_price,
             ' [SIM]' if sim else '')
    return trade_id


def close_trade(client, db_path, trade, current_price, reason, sim=False):
    tick_data   = TICK_INFO.get(trade['symbol'], TICK_INFO['ES'])
    point_value = tick_data['point_value']
    pnl         = calc_pnl(trade['direction'], trade['entry_price'], current_price,
                           trade['contracts'], point_value)
    if not sim and trade.get('order_id') != 'SIM':
        action = 'Sell' if trade['direction'] == 'long' else 'Buy'
        try:
            client.place_order(trade['symbol'], action, trade['contracts'])
        except Exception:
            log.exception('Failed to close %s trade id=%s', trade['symbol'], trade['id'])
            return
    update_trade_closed(
        db_path, trade['id'],
        close_price=current_price,
        close_reason=reason,
        close_ts=datetime.now(timezone.utc).isoformat(),
        pnl=pnl,
    )
    log.info('Closed: %s %s reason=%s price=%.2f pnl=%.2f',
             trade['direction'], trade['symbol'], reason, current_price, pnl)
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/futures/test_futures_trader.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/futures/trader.py tests/futures/test_futures_trader.py
git commit -m "feat: futures order placement and trade closing"
```

---

### Task 6: Futures manager (position monitoring)

**Files:**
- Create: `bot/futures/manager.py`
- Test: `tests/futures/test_futures_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/futures/test_futures_manager.py
import os, tempfile, pytest
from unittest.mock import MagicMock
from bot.futures.db import init_db, insert_trade, get_open_trades
from bot.futures.manager import manage_futures_positions

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)

def test_closes_on_stop_loss(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 4997.5}, sim=True)
    assert len(get_open_trades(tmp_db)) == 0

def test_closes_on_profit_target(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'orb', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 5004.5}, sim=True)
    assert len(get_open_trades(tmp_db)) == 0

def test_no_close_within_range(tmp_db):
    insert_trade(tmp_db, {
        'symbol': 'ES', 'strategy': 'vwap', 'direction': 'long',
        'entry_price': 5000.0, 'entry_ts': '2026-01-01T10:00:00',
        'stop_price': 4998.0, 'target_price': 5004.0,
        'contracts': 1, 'order_id': 'SIM', 'status': 'open',
    })
    manage_futures_positions(MagicMock(), tmp_db, current_prices={'ES': 5001.0}, sim=True)
    assert len(get_open_trades(tmp_db)) == 1
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_futures_manager.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement manager.py**

```python
# bot/futures/manager.py
import logging
from bot.futures.db import get_open_trades, update_trade_price
from bot.futures.risk import should_exit
from bot.futures.trader import close_trade

log = logging.getLogger(__name__)


def manage_futures_positions(client, db_path, current_prices: dict, sim=False):
    open_trades = get_open_trades(db_path)
    if not open_trades:
        return
    for trade in open_trades:
        symbol        = trade['symbol']
        current_price = current_prices.get(symbol)
        if current_price is None:
            log.warning('No price for %s — skipping', symbol)
            continue
        update_trade_price(db_path, trade['id'], current_price)
        reason = should_exit(
            trade['direction'],
            current_price,
            float(trade['stop_price']),
            float(trade['target_price']),
        )
        if reason:
            close_trade(client, db_path, trade, current_price, reason, sim=sim)
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/futures/test_futures_manager.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/futures/manager.py tests/futures/test_futures_manager.py
git commit -m "feat: futures position manager with stop/target monitoring"
```

---

### Task 7: Futures main (scheduler + orchestration)

**Files:**
- Create: `bot/futures/main.py`
- Modify: `.env`

- [ ] **Step 1: Add env vars to .env**

Open `.env` and append:

```
# Tradovate Futures Bot
TV_USERNAME=your_tradovate_username
TV_PASSWORD=your_tradovate_password
TV_CID=your_client_id_from_dev_portal
TV_SEC=your_client_secret_from_dev_portal
TV_DEVICE_ID=sharp-bot-futures-001
TV_DEMO=true
```

- [ ] **Step 2: Create main.py**

```python
# bot/futures/main.py
import logging
from datetime import datetime
from datetime import time as _Time
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from bot.futures.config import (
    FUTURES_DB_PATH, SYMBOLS, TICK_INFO, STRATEGY_PARAMS, RISK_RULES,
    TIMEZONE, MARKET_OPEN, MARKET_CLOSE, ORB_END,
    TV_USERNAME, TV_PASSWORD, TV_CID, TV_SEC, TV_DEVICE_ID, TV_DEMO,
)
from bot.futures.db import (
    init_db, insert_signal, get_daily_pnl, get_setting,
    insert_snapshot,
)
from bot.futures.tradovate_client import TradovateClient
from bot.futures.strategy import VWAPState, ORBState, calc_vwap, check_vwap_signal, check_orb_signal
from bot.futures.risk import is_daily_loss_limit_hit
from bot.futures.trader import place_entry
from bot.futures.manager import manage_futures_positions

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ET = pytz.timezone(TIMEZONE)

_vwap_states: dict = {}
_orb_states:  dict = {}


def _is_market_hours():
    now = datetime.now(ET).time()
    oh, om = map(int, MARKET_OPEN.split(':'))
    ch, cm = map(int, MARKET_CLOSE.split(':'))
    return _Time(oh, om) <= now <= _Time(ch, cm)


def _is_orb_period():
    now = datetime.now(ET).time()
    oh, om = map(int, MARKET_OPEN.split(':'))
    eh, em = map(int, ORB_END.split(':'))
    return _Time(oh, om) <= now < _Time(eh, em)


def _orb_end_minute():
    h, m = map(int, ORB_END.split(':'))
    return h * 60 + m


def _now_minute():
    now = datetime.now(ET)
    return now.hour * 60 + now.minute


def _reset_daily_state():
    global _vwap_states, _orb_states
    _vwap_states = {s: VWAPState() for s in SYMBOLS}
    _orb_states  = {s: ORBState()  for s in SYMBOLS}
    log.info('Daily state reset — VWAP and ORB cleared')


def job_scan(client):
    if not _is_market_hours():
        return

    today     = datetime.now(ET).strftime('%Y-%m-%d')
    daily_pnl = get_daily_pnl(FUTURES_DB_PATH, today)
    if is_daily_loss_limit_hit(daily_pnl, RISK_RULES['daily_loss_limit']):
        log.warning('Daily loss limit hit ($%.2f) — skipping scan', daily_pnl)
        return

    sim = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'

    try:
        prices = client.get_current_prices(SYMBOLS, timeout=20)
    except Exception:
        log.exception('Failed to fetch prices')
        return

    orb_period  = _is_orb_period()
    orb_end_min = _orb_end_minute()
    now_min     = _now_minute()
    now_iso     = datetime.now(ET).isoformat()

    for symbol in SYMBOLS:
        price = prices.get(symbol)
        if price is None:
            continue

        tick = TICK_INFO[symbol]['tick']
        vwap_state = _vwap_states.setdefault(symbol, VWAPState())
        orb_state  = _orb_states.setdefault(symbol, ORBState())

        vwap_state.add_bar(price=price, volume=1)

        if not orb_state._ready and now_min >= orb_end_min:
            orb_state.set_ready()

        if orb_period:
            orb_state.update(price=price, ts_minute=now_min)
            continue

        vwap    = calc_vwap(vwap_state)
        signal  = None
        strategy = None

        if vwap is not None:
            direction = check_vwap_signal(price, vwap, STRATEGY_PARAMS['vwap_deviation_pct'])
            if direction:
                signal, strategy = direction, 'vwap'

        orb_dir = check_orb_signal(price, orb_state, orb_end_min,
                                    STRATEGY_PARAMS['orb_min_range_ticks'], tick)
        if orb_dir:
            signal, strategy = orb_dir, 'orb'

        if signal is None:
            continue

        signal_id = insert_signal(FUTURES_DB_PATH, {
            'ts': now_iso, 'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'vwap': vwap,
            'orb_high': orb_state.high if orb_state._ready else None,
            'orb_low':  orb_state.low  if orb_state._ready else None,
            'traded': 0,
        })
        log.info('Signal: %s %s %s @ %.2f', strategy, signal, symbol, price)
        place_entry(client, FUTURES_DB_PATH, {
            'symbol': symbol, 'strategy': strategy,
            'direction': signal, 'price': price, 'signal_id': signal_id,
        }, contracts=1, sim=sim)


def job_manage(client):
    if not _is_market_hours():
        return
    try:
        prices = client.get_current_prices(SYMBOLS, timeout=15)
        sim    = get_setting(FUTURES_DB_PATH, 'trading_mode', 'sim') == 'sim'
        manage_futures_positions(client, FUTURES_DB_PATH, current_prices=prices, sim=sim)
    except Exception:
        log.exception('Manager error')


def job_snapshot(client):
    try:
        bal = client.get_account_balance()
        insert_snapshot(FUTURES_DB_PATH, {
            'ts':                 datetime.now(ET).isoformat(),
            'net_liq':            float(bal.get('netLiquidatingValue', 0)),
            'cash':               float(bal.get('cashBalance', 0)),
            'open_pnl':           float(bal.get('openTradeEquity', 0)),
            'realized_pnl_today': float(bal.get('realizedPnL', 0)),
        })
    except Exception:
        log.exception('Snapshot error')


def main():
    init_db(FUTURES_DB_PATH)
    _reset_daily_state()

    client = TradovateClient(TV_USERNAME, TV_PASSWORD, TV_CID, TV_SEC,
                             demo=TV_DEMO, device_id=TV_DEVICE_ID)
    try:
        client.connect()
    except Exception:
        log.exception('Failed to connect to Tradovate — exiting')
        return

    def _scan():     job_scan(client)
    def _manage():   job_manage(client)
    def _snapshot(): job_snapshot(client)
    def _reset():    _reset_daily_state()

    scheduler = BlockingScheduler(timezone=ET)
    scheduler.add_job(_scan,     IntervalTrigger(seconds=45, timezone=ET), id='scan')
    scheduler.add_job(_manage,   IntervalTrigger(seconds=15, timezone=ET), id='manage')
    scheduler.add_job(_snapshot, CronTrigger(day_of_week='mon-fri', hour='10-15', minute=0, timezone=ET), id='snapshot')
    scheduler.add_job(_reset,    CronTrigger(day_of_week='mon-fri', hour=9, minute=29, timezone=ET), id='reset')

    log.info('Futures bot running [%s]. Ctrl+C to stop.', 'DEMO' if TV_DEMO else 'LIVE')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info('Shutting down.')
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Commit**

```bash
git add bot/futures/main.py .env
git commit -m "feat: futures bot main scheduler and orchestration"
```

---

### Task 8: Futures web route

**Files:**
- Modify: `web/routes/futures.js`

- [ ] **Step 1: Replace futures.js with full route**

```javascript
// web/routes/futures.js
const router   = require('express').Router();
const path     = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');

const DB_PATH = path.join(__dirname, '../../bot/data/futures.db');

function getDb(readonly = true) {
  try {
    return new Database(DB_PATH, { readonly, fileMustExist: true });
  } catch (err) {
    if (err.code !== 'SQLITE_CANTOPEN') {
      console.error('[futures] DB open error:', err.message);
    }
    return null;
  }
}

router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  let openTrades    = [];
  let closedTrades  = [];
  let recentSignals = [];
  let accountSnap   = null;
  let allTimePnl    = null;
  let todayPnl      = null;
  let botOnline     = false;

  if (db) {
    try {
      openTrades = db.prepare(
        "SELECT * FROM futures_trades WHERE status='open' ORDER BY entry_ts DESC"
      ).all();
      closedTrades = db.prepare(
        "SELECT * FROM futures_trades WHERE status='closed' ORDER BY close_ts DESC LIMIT 50"
      ).all();
      recentSignals = db.prepare(
        "SELECT * FROM futures_signals ORDER BY id DESC LIMIT 20"
      ).all();
      accountSnap = db.prepare(
        "SELECT * FROM futures_snapshots ORDER BY id DESC LIMIT 1"
      ).get() || null;

      const today    = new Date().toISOString().slice(0, 10);
      const todayRow = db.prepare(
        "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed' AND close_ts LIKE ?"
      ).get(`${today}%`);
      todayPnl = todayRow ? parseFloat(todayRow.total) : 0;

      const allRow = db.prepare(
        "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed'"
      ).get();
      allTimePnl = allRow ? parseFloat(allRow.total) : 0;
      botOnline  = true;
    } catch (err) {
      console.error('[futures] DB query error:', err.message);
    } finally {
      db.close();
    }
  }

  res.render('futures', {
    user: req.session.user,
    openTrades,
    closedTrades,
    recentSignals,
    accountSnap,
    botOnline,
    todayPnl:   botOnline ? todayPnl   : null,
    allTimePnl: botOnline ? allTimePnl : null,
  });
});

module.exports = router;
```

- [ ] **Step 2: Restart web server and verify `/futures` loads**

```
node web/server.js
```

Navigate to `http://localhost:3000/futures` — should render without errors.

- [ ] **Step 3: Commit**

```bash
git add web/routes/futures.js
git commit -m "feat: futures web route reading futures.db"
```

---

### Task 9: Futures dashboard view

**Files:**
- Modify: `web/views/futures.ejs`

- [ ] **Step 1: Replace futures.ejs with full dashboard**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="15">
  <title>Futures Trading — Sharp Bot</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/style.css?v=6">
  <style>
    .fut-stat-bar { display:flex; gap:1.25rem; flex-wrap:wrap; margin-bottom:1.5rem; }
    .fut-stat { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:1rem 1.4rem; min-width:130px; }
    .fut-stat-label { font-size:0.70rem; color:var(--text2); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px; }
    .fut-stat-value { font-family:'Space Mono',monospace; font-size:1.2rem; font-weight:700; }
    .fut-stat-value.pos { color:#00d4a0; }
    .fut-stat-value.neg { color:#ff4f4f; }
    .opt-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
    .opt-table th { text-align:left; padding:8px 12px; color:var(--text2); font-weight:600; font-size:0.70rem; text-transform:uppercase; letter-spacing:0.05em; border-bottom:1px solid var(--border); }
    .opt-table td { padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.04); }
    tr.row-profit td { color:#00d4a0; }
    tr.row-loss   td { color:#ff4f4f; }
    .badge { display:inline-block; font-size:0.63rem; padding:2px 7px; border-radius:4px; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; }
    .b-long  { background:rgba(0,212,160,0.12);  color:#00d4a0; }
    .b-short { background:rgba(255,79,79,0.12);   color:#ff4f4f; }
    .b-vwap  { background:rgba(100,180,255,0.12); color:#64b4ff; }
    .b-orb   { background:rgba(200,130,255,0.12); color:#c882ff; }
    .b-open  { background:rgba(0,212,160,0.12);   color:#00d4a0; }
    .sect-hdr { font-size:0.82rem; font-weight:700; color:var(--accent); margin:0 0 0.75rem; text-transform:uppercase; letter-spacing:0.07em; }
    .offline-bar { background:rgba(255,79,79,0.08); border:1px solid rgba(255,79,79,0.25); border-radius:8px; padding:0.7rem 1rem; font-size:0.82rem; color:#ff4f4f; margin-bottom:1.25rem; }
    details summary { cursor:pointer; color:var(--text2); font-size:0.82rem; padding:4px 0; user-select:none; }
    details summary:hover { color:var(--text); }
    details[open] summary { color:var(--accent); }
    .pnl-bar-wrap { width:90px; height:5px; background:rgba(255,255,255,0.08); border-radius:3px; margin-top:4px; }
    .pnl-bar-fill { height:5px; border-radius:3px; }
    .pnl-bar-fill.profit { background:#00d4a0; }
    .pnl-bar-fill.loss   { background:#ff4f4f; }
  </style>
</head>
<body>
  <%
  function money(n) {
    if (n == null) return '—';
    const v = parseFloat(n);
    return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function pclass(n) { return n == null ? '' : parseFloat(n) >= 0 ? 'pos' : 'neg'; }
  function dirBadge(d) {
    if (d === 'long')  return '<span class="badge b-long">Long</span>';
    if (d === 'short') return '<span class="badge b-short">Short</span>';
    return '<span class="badge">' + (d||'?') + '</span>';
  }
  function stratBadge(s) {
    if (s === 'vwap') return '<span class="badge b-vwap">VWAP</span>';
    if (s === 'orb')  return '<span class="badge b-orb">ORB</span>';
    return '<span class="badge">' + (s||'?') + '</span>';
  }
  %>

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
        <button class="nav-dropdown-trigger active">Trading <span class="dd-arrow">▼</span></button>
        <div class="nav-dropdown-menu">
          <a href="/options">Options</a>
          <a href="/futures" class="active">Futures</a>
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
    <div class="page-header"><h2>Futures Trading</h2></div>

    <% if (!botOnline) { %>
    <div class="offline-bar">Bot offline — run <code>python -m bot.futures.main</code> to start.</div>
    <% } %>

    <!-- Account Stats -->
    <div class="fut-stat-bar">
      <div class="fut-stat">
        <div class="fut-stat-label">Net Liq</div>
        <div class="fut-stat-value <%= accountSnap ? pclass(accountSnap.net_liq) : '' %>">
          <%= accountSnap ? money(accountSnap.net_liq) : '—' %>
        </div>
      </div>
      <div class="fut-stat">
        <div class="fut-stat-label">Today P&amp;L</div>
        <div class="fut-stat-value <%= pclass(todayPnl) %>">
          <%= todayPnl != null ? (todayPnl >= 0 ? '+' : '') + money(todayPnl) : '—' %>
        </div>
      </div>
      <div class="fut-stat">
        <div class="fut-stat-label">All-time P&amp;L</div>
        <div class="fut-stat-value <%= pclass(allTimePnl) %>">
          <%= allTimePnl != null ? (allTimePnl >= 0 ? '+' : '') + money(allTimePnl) : '—' %>
        </div>
      </div>
      <div class="fut-stat">
        <div class="fut-stat-label">Open Positions</div>
        <div class="fut-stat-value"><%= openTrades ? openTrades.length : 0 %></div>
      </div>
    </div>

    <!-- Open Positions -->
    <div class="card" style="margin-bottom:1.25rem;">
      <p class="sect-hdr">Open Positions</p>
      <% if (openTrades && openTrades.length > 0) { %>
      <div style="overflow-x:auto;">
        <table class="opt-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Strategy</th><th>Direction</th>
              <th>Entry</th><th>Current</th><th>Stop</th><th>Target</th>
              <th>Unrealized P&amp;L</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            <% for (const t of openTrades) { %>
            <%
              const curr  = t.current_price != null ? parseFloat(t.current_price) : null;
              const entry = parseFloat(t.entry_price);
              const pv    = t.symbol === 'NQ' ? 20 : 50;
              const unreal = curr != null
                ? (t.direction === 'long' ? curr - entry : entry - curr) * t.contracts * pv
                : null;
              const barPct = unreal != null ? Math.min(100, Math.max(0, Math.abs(unreal) / (RISK_RULES_TARGET || 200) * 100)) : 0;
            %>
            <tr>
              <td style="font-weight:700;"><%= t.symbol %></td>
              <td><%- stratBadge(t.strategy) %></td>
              <td><%- dirBadge(t.direction) %></td>
              <td style="font-family:'Space Mono',monospace;"><%= entry.toFixed(2) %></td>
              <td style="font-family:'Space Mono',monospace;"><%= curr != null ? curr.toFixed(2) : '—' %></td>
              <td style="font-family:'Space Mono',monospace;color:var(--text2);"><%= parseFloat(t.stop_price).toFixed(2) %></td>
              <td style="font-family:'Space Mono',monospace;color:var(--text2);"><%= parseFloat(t.target_price).toFixed(2) %></td>
              <td style="font-family:'Space Mono',monospace;font-weight:700;">
                <% if (unreal != null) { %>
                  <span style="color:<%= unreal >= 0 ? '#00d4a0' : '#ff4f4f' %>">
                    <%= unreal >= 0 ? '+' : '' %><%= money(unreal) %>
                  </span>
                  <div class="pnl-bar-wrap">
                    <div class="pnl-bar-fill <%= unreal >= 0 ? 'profit' : 'loss' %>" style="width:<%= Math.min(100, Math.abs(unreal)/100*100) %>%"></div>
                  </div>
                <% } else { %>—<% } %>
              </td>
              <td><span class="badge b-open">Open</span></td>
            </tr>
            <% } %>
          </tbody>
        </table>
      </div>
      <% } else { %>
      <p style="color:var(--text2);font-size:0.82rem;padding:0.5rem 0;">No open positions.</p>
      <% } %>
    </div>

    <!-- Signal Feed -->
    <div class="card" style="margin-bottom:1.25rem;">
      <p class="sect-hdr">Signal Feed</p>
      <% if (recentSignals && recentSignals.length > 0) { %>
      <div style="overflow-x:auto;">
        <table class="opt-table">
          <thead>
            <tr>
              <th>Time</th><th>Symbol</th><th>Strategy</th><th>Direction</th>
              <th>Price</th><th>VWAP</th><th>ORB High</th><th>ORB Low</th><th></th>
            </tr>
          </thead>
          <tbody>
            <% for (const s of recentSignals) { %>
            <tr>
              <td style="color:var(--text2);font-family:'Space Mono',monospace;font-size:0.75rem;">
                <%= s.ts ? new Date(s.ts).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—' %>
              </td>
              <td style="font-weight:700;"><%= s.symbol %></td>
              <td><%- stratBadge(s.strategy) %></td>
              <td><%- dirBadge(s.direction) %></td>
              <td style="font-family:'Space Mono',monospace;"><%= s.price != null ? parseFloat(s.price).toFixed(2) : '—' %></td>
              <td style="font-family:'Space Mono',monospace;color:var(--text2);"><%= s.vwap != null ? parseFloat(s.vwap).toFixed(2) : '—' %></td>
              <td style="font-family:'Space Mono',monospace;color:var(--text2);"><%= s.orb_high != null ? parseFloat(s.orb_high).toFixed(2) : '—' %></td>
              <td style="font-family:'Space Mono',monospace;color:var(--text2);"><%= s.orb_low  != null ? parseFloat(s.orb_low).toFixed(2)  : '—' %></td>
              <td><% if (s.traded == 1) { %><span class="badge b-open">Traded</span><% } %></td>
            </tr>
            <% } %>
          </tbody>
        </table>
      </div>
      <% } else { %>
      <p style="color:var(--text2);font-size:0.82rem;padding:0.5rem 0;">No signals yet. Bot scans every 45 seconds during market hours (9:30 AM–4:00 PM ET).</p>
      <% } %>
    </div>

    <!-- Trade History -->
    <div class="card" style="margin-bottom:1.25rem;">
      <details>
        <summary>
          <span class="sect-hdr" style="display:inline;">Trade History</span>
          &nbsp;<span style="font-size:0.75rem;color:var(--text2);">(<%= closedTrades ? closedTrades.length : 0 %> closed)</span>
        </summary>
        <div style="margin-top:1rem;">
          <% if (closedTrades && closedTrades.length > 0) { %>
          <div style="overflow-x:auto;">
            <table class="opt-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Strategy</th><th>Direction</th>
                  <th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th><th>Date</th>
                </tr>
              </thead>
              <tbody>
                <% for (const t of closedTrades) { %>
                <tr class="<%= parseFloat(t.pnl||0) >= 0 ? 'row-profit' : 'row-loss' %>">
                  <td style="font-weight:700;"><%= t.symbol %></td>
                  <td><%- stratBadge(t.strategy) %></td>
                  <td><%- dirBadge(t.direction) %></td>
                  <td style="font-family:'Space Mono',monospace;"><%= parseFloat(t.entry_price).toFixed(2) %></td>
                  <td style="font-family:'Space Mono',monospace;"><%= t.close_price != null ? parseFloat(t.close_price).toFixed(2) : '—' %></td>
                  <td style="font-family:'Space Mono',monospace;font-weight:700;">
                    <%= parseFloat(t.pnl||0) >= 0 ? '+' : '' %><%= money(t.pnl) %>
                  </td>
                  <td style="color:var(--text2);font-size:0.78rem;"><%= t.close_reason || '—' %></td>
                  <td style="color:var(--text2);font-size:0.78rem;">
                    <%= t.close_ts ? new Date(t.close_ts).toLocaleDateString() : '—' %>
                  </td>
                </tr>
                <% } %>
              </tbody>
            </table>
          </div>
          <% } else { %>
          <p style="color:var(--text2);font-size:0.82rem;padding:0.5rem 0;">No closed trades yet.</p>
          <% } %>
        </div>
      </details>
    </div>
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r134/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/vanta@latest/dist/vanta.net.min.js"></script>
  <script>
    if (typeof VANTA !== 'undefined') {
      VANTA.NET({
        el: '#vanta-bg', mouseControls: true, touchControls: false, gyroControls: false,
        color: 0x00ffcc, backgroundColor: 0x020509,
        points: 16.0, maxDistance: 22.0, spacing: 12.0, showDots: true,
      });
    }
  </script>
  <script src="/nav.js?v=4"></script>
</body>
</html>
```

- [ ] **Step 2: Verify in browser**

Restart web server, navigate to `http://localhost:3000/futures`. Should show full dashboard with Nav, stats bar, Open Positions, Signal Feed, and Trade History — all showing empty states cleanly.

- [ ] **Step 3: Commit**

```bash
git add web/views/futures.ejs
git commit -m "feat: futures dashboard — positions, signals, trade history"
```

---

### Task 10: News & economic calendar integration

**What it does:**
- `bot/futures/news.py` fetches two things every hour:
  1. **Economic calendar** — high-impact US events today (FOMC, NFP, CPI, GDP, PMI) via the Nasdaq free calendar API (same source as `bot/calendar.py`)
  2. **Live headlines** — top 5 market-moving news items via Alpha Vantage `NEWS_SENTIMENT` (free, 25 calls/day)
- Both are stored in `futures.db` (`futures_news` table)
- `bot/futures/main.py` `job_scan` checks `is_news_blackout()` against today's calendar events before entering any trade
- The futures dashboard shows a **News & Events** card with upcoming blackout windows and recent headlines

**Files:**
- Create: `bot/futures/news.py`
- Modify: `bot/futures/db.py` — add `futures_news` table
- Modify: `bot/futures/main.py` — wire `job_news` scheduler job + pass event times to `is_news_blackout`
- Modify: `web/routes/futures.js` — query `futures_news` and pass to view
- Modify: `web/views/futures.ejs` — add News & Events card
- Modify: `.env` — add `AV_API_KEY`
- Test: `tests/futures/test_news.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/futures/test_news.py
import pytest
from unittest.mock import patch, MagicMock
from bot.futures.news import parse_econ_events, parse_av_headlines, HIGH_IMPACT_KEYWORDS

def test_parse_econ_events_filters_high_impact():
    raw = [
        {'description': 'FOMC Meeting', 'date': '2026-05-14', 'time': '14:00', 'impact': 'High'},
        {'description': 'Retail Sales', 'date': '2026-05-14', 'time': '08:30', 'impact': 'Low'},
        {'description': 'CPI Report',   'date': '2026-05-14', 'time': '08:30', 'impact': 'High'},
    ]
    events = parse_econ_events(raw, date_str='2026-05-14')
    assert len(events) == 2
    assert all(e['impact'] == 'High' for e in events)

def test_parse_av_headlines_returns_list():
    raw_av = {
        'feed': [
            {'title': 'Fed raises rates', 'url': 'http://x.com', 'time_published': '20260514T143000',
             'summary': 'The Fed raised rates by 25bps.', 'overall_sentiment_label': 'Bearish'},
            {'title': 'Strong jobs report', 'url': 'http://y.com', 'time_published': '20260514T083000',
             'summary': 'NFP beat expectations.', 'overall_sentiment_label': 'Bullish'},
        ]
    }
    headlines = parse_av_headlines(raw_av, limit=5)
    assert len(headlines) == 2
    assert headlines[0]['title'] == 'Fed raises rates'
    assert headlines[0]['sentiment'] == 'Bearish'

def test_high_impact_keywords_coverage():
    assert 'FOMC' in HIGH_IMPACT_KEYWORDS
    assert 'CPI'  in HIGH_IMPACT_KEYWORDS
    assert 'NFP'  in HIGH_IMPACT_KEYWORDS
```

- [ ] **Step 2: Run to verify failure**

```
python -m pytest tests/futures/test_news.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add `futures_news` table to db.py**

Add to `bot/futures/db.py` after `_CREATE_SETTINGS`:

```python
_CREATE_NEWS = """
CREATE TABLE IF NOT EXISTS futures_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_ts TEXT,
    news_type TEXT,        -- 'event' or 'headline'
    title TEXT,
    event_ts TEXT,         -- ISO datetime of the event/article
    impact TEXT,           -- 'High', 'Medium', 'Low' for events; NULL for headlines
    sentiment TEXT,        -- NULL for events; 'Bullish'/'Bearish'/'Neutral' for headlines
    url TEXT
)
"""
```

Add `conn.execute(_CREATE_NEWS)` inside `init_db` after the other tables.

Add these functions to `bot/futures/db.py`:

```python
def upsert_news(db_path, items: list):
    """Replace today's news records with fresh fetch."""
    today = items[0]['fetched_ts'][:10] if items else ''
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM futures_news WHERE fetched_ts LIKE ?", (f"{today}%",))
        for item in items:
            conn.execute(
                "INSERT INTO futures_news (fetched_ts, news_type, title, event_ts, impact, sentiment, url) "
                "VALUES (:fetched_ts, :news_type, :title, :event_ts, :impact, :sentiment, :url)",
                item,
            )


def get_recent_news(db_path, limit=20):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM futures_news ORDER BY event_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_today_event_times(db_path, date_str: str) -> list:
    """Return ISO datetimes of high-impact economic events today."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT event_ts FROM futures_news WHERE news_type='event' AND impact='High' AND event_ts LIKE ?",
            (f"{date_str}%",)
        ).fetchall()
        return [r['event_ts'] for r in rows]
```

- [ ] **Step 4: Create news.py**

```python
# bot/futures/news.py
import logging
from datetime import datetime, timezone
import httpx

log = logging.getLogger(__name__)

HIGH_IMPACT_KEYWORDS = {
    'FOMC', 'Federal Reserve', 'Fed Rate', 'Interest Rate Decision',
    'CPI', 'Consumer Price Index', 'Inflation',
    'NFP', 'Non-Farm Payroll', 'Nonfarm Payroll', 'Jobs Report',
    'GDP', 'Gross Domestic Product',
    'PMI', 'ISM',
    'Retail Sales', 'PCE', 'PPI',
    'Unemployment Rate', 'Initial Jobless',
}

_NASDAQ_URL = 'https://api.nasdaq.com/api/calendar/economicevents'
_NASDAQ_HEADERS = {'Accept': 'application/json, text/plain, */*',
                   'User-Agent': 'Mozilla/5.0'}


def _fetch_nasdaq_events(date_str: str) -> list:
    try:
        resp = httpx.get(
            _NASDAQ_URL,
            params={'date': date_str},
            headers=_NASDAQ_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', {}).get('rows', [])
    except Exception as exc:
        log.warning('Nasdaq calendar fetch failed: %s', exc)
        return []


def parse_econ_events(raw_rows: list, date_str: str) -> list:
    """Filter raw Nasdaq calendar rows to high-impact events only."""
    events = []
    for row in raw_rows:
        impact = row.get('impact', '')
        if impact != 'High':
            continue
        desc = row.get('description', '') or row.get('eventName', '')
        time_str = row.get('time', '08:30').strip() or '08:30'
        try:
            event_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        except ValueError:
            event_dt = datetime.fromisoformat(f"{date_str}T08:30:00")
        events.append({
            'news_type': 'event',
            'title':     desc,
            'event_ts':  event_dt.isoformat(),
            'impact':    impact,
            'sentiment': None,
            'url':       None,
        })
    return events


def _fetch_av_headlines(api_key: str, tickers='SPY,QQQ,ES', limit=5) -> dict:
    try:
        resp = httpx.get(
            'https://www.alphavantage.co/query',
            params={
                'function': 'NEWS_SENTIMENT',
                'tickers':  tickers,
                'limit':    limit,
                'apikey':   api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning('Alpha Vantage news fetch failed: %s', exc)
        return {}


def parse_av_headlines(raw: dict, limit=5) -> list:
    headlines = []
    for item in raw.get('feed', [])[:limit]:
        tp = item.get('time_published', '')
        try:
            # Format: 20260514T143000
            event_dt = datetime.strptime(tp, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
            event_iso = event_dt.isoformat()
        except ValueError:
            event_iso = datetime.now(timezone.utc).isoformat()
        headlines.append({
            'news_type': 'headline',
            'title':     item.get('title', ''),
            'event_ts':  event_iso,
            'impact':    None,
            'sentiment': item.get('overall_sentiment_label', 'Neutral'),
            'url':       item.get('url', ''),
        })
    return headlines


def fetch_and_store_news(db_path: str, av_api_key: str):
    """Fetch economic events + headlines and write to DB. Called hourly."""
    from bot.futures.db import upsert_news
    now_iso  = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    items = []

    raw_events = _fetch_nasdaq_events(date_str)
    events     = parse_econ_events(raw_events, date_str)
    for e in events:
        e['fetched_ts'] = now_iso
    items.extend(events)
    log.info('Fetched %d high-impact economic events for %s', len(events), date_str)

    if av_api_key:
        raw_av    = _fetch_av_headlines(av_api_key, limit=5)
        headlines = parse_av_headlines(raw_av, limit=5)
        for h in headlines:
            h['fetched_ts'] = now_iso
        items.extend(headlines)
        log.info('Fetched %d news headlines', len(headlines))

    if items:
        upsert_news(db_path, items)
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/futures/test_news.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 6: Wire into main.py**

Add import at top of `bot/futures/main.py`:

```python
from bot.futures.news import fetch_and_store_news
from bot.futures.db import get_today_event_times
```

Add `AV_API_KEY` to config imports:

```python
from bot.futures.config import (
    ...
    TV_DEMO,
)
from bot.futures import config as _cfg
```

Add `AV_API_KEY` to `bot/futures/config.py`:

```python
AV_API_KEY = os.environ.get('AV_API_KEY', '')
```

Add `.env` entry:

```
AV_API_KEY=your_alpha_vantage_key
```

Get a free key at alphavantage.co — takes 30 seconds, no credit card.

Add `job_news` function in `bot/futures/main.py` after `job_snapshot`:

```python
def job_news(client=None):
    from bot.futures.config import AV_API_KEY
    try:
        fetch_and_store_news(FUTURES_DB_PATH, AV_API_KEY)
    except Exception:
        log.exception('News fetch error')
```

Replace the `is_news_blackout` call in `job_scan` — update the blackout check to use live DB events:

```python
    # In job_scan, before the symbol loop:
    today      = datetime.now(ET).strftime('%Y-%m-%d')
    now_iso    = datetime.now(ET).isoformat()
    event_times = get_today_event_times(FUTURES_DB_PATH, today)
    from bot.futures.risk import is_news_blackout
    if is_news_blackout(now_iso, RISK_RULES['news_blackout_minutes'], event_times):
        log.info('News blackout active — skipping scan')
        return
```

Add scheduler job in `main()` after existing jobs:

```python
    # Fetch news hourly + once at startup
    scheduler.add_job(_news,     CronTrigger(day_of_week='mon-fri', hour='8-16', minute=0, timezone=ET), id='news')
    scheduler.add_job(job_news,  'date', run_date=datetime.now(ET), id='news_startup')
```

Add `def _news(): job_news()` with the other closures.

- [ ] **Step 7: Update web route to include news**

In `web/routes/futures.js`, add inside the `if (db)` block after `allTimePnl`:

```javascript
      const recentNews = db.prepare(
        "SELECT * FROM futures_news ORDER BY event_ts DESC LIMIT 15"
      ).all();
      // separate events from headlines
      res.locals.futureNews = recentNews;
```

Add `recentNews` to the `res.render` call:

```javascript
  res.render('futures', {
    ...
    recentNews: botOnline ? recentNews : [],
  });
```

- [ ] **Step 8: Add News & Events card to futures.ejs**

Add this card between the Signal Feed and Trade History sections in `web/views/futures.ejs`:

```html
    <!-- News & Events -->
    <div class="card" style="margin-bottom:1.25rem;">
      <p class="sect-hdr">News & Events</p>
      <% if (typeof recentNews !== 'undefined' && recentNews.length > 0) { %>
      <div style="overflow-x:auto;">
        <table class="opt-table">
          <thead>
            <tr><th>Time</th><th>Type</th><th>Title</th><th>Impact / Sentiment</th></tr>
          </thead>
          <tbody>
            <% for (const n of recentNews) { %>
            <tr>
              <td style="color:var(--text2);font-family:'Space Mono',monospace;font-size:0.75rem;white-space:nowrap;">
                <%= n.event_ts ? new Date(n.event_ts).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—' %>
              </td>
              <td>
                <% if (n.news_type === 'event') { %>
                  <span class="badge" style="background:rgba(245,166,35,0.12);color:#f5a623;">EVENT</span>
                <% } else { %>
                  <span class="badge" style="background:rgba(100,180,255,0.12);color:#64b4ff;">NEWS</span>
                <% } %>
              </td>
              <td style="font-size:0.80rem;max-width:420px;">
                <% if (n.url) { %>
                  <a href="<%- n.url %>" target="_blank" style="color:var(--text);text-decoration:none;"><%= n.title %></a>
                <% } else { %>
                  <%= n.title %>
                <% } %>
              </td>
              <td>
                <% if (n.impact) { %>
                  <span style="color:<%= n.impact==='High'?'#ff4f4f':n.impact==='Medium'?'#f5a623':'var(--text2)' %>;font-size:0.78rem;font-weight:600;"><%= n.impact %></span>
                <% } else if (n.sentiment) { %>
                  <span style="color:<%= n.sentiment==='Bullish'?'#00d4a0':n.sentiment==='Bearish'?'#ff4f4f':'var(--text2)' %>;font-size:0.78rem;"><%= n.sentiment %></span>
                <% } %>
              </td>
            </tr>
            <% } %>
          </tbody>
        </table>
      </div>
      <% } else { %>
      <p style="color:var(--text2);font-size:0.82rem;padding:0.5rem 0;">No news fetched yet. Updates hourly during market hours.</p>
      <% } %>
    </div>
```

- [ ] **Step 9: Commit**

```bash
git add bot/futures/news.py bot/futures/db.py bot/futures/main.py bot/futures/config.py \
        web/routes/futures.js web/views/futures.ejs tests/futures/test_news.py .env
git commit -m "feat: futures news feed — economic calendar blackouts + Alpha Vantage headlines"
```
