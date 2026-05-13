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
