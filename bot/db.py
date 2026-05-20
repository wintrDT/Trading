import contextlib
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
    traded INTEGER DEFAULT 0,
    trade_type TEXT DEFAULT 'premium'
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
    current_mark REAL,
    close_credit REAL,
    close_ts TEXT,
    close_reason TEXT,
    status TEXT DEFAULT 'open',
    contracts INTEGER DEFAULT 1,
    order_id TEXT,
    trade_type TEXT DEFAULT 'premium'
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

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS bot_settings (
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
        conn.execute(_CREATE_SCANS)
        conn.execute(_CREATE_TRADES)
        conn.execute(_CREATE_SNAPSHOTS)
        conn.execute(_CREATE_SETTINGS)
        # Migrations for existing DBs
        trade_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if 'current_mark' not in trade_cols:
            conn.execute("ALTER TABLE trades ADD COLUMN current_mark REAL")
        if 'trade_type' not in trade_cols:
            conn.execute("ALTER TABLE trades ADD COLUMN trade_type TEXT DEFAULT 'premium'")
        scan_cols = {r[1] for r in conn.execute("PRAGMA table_info(scans)").fetchall()}
        if 'trade_type' not in scan_cols:
            conn.execute("ALTER TABLE scans ADD COLUMN trade_type TEXT DEFAULT 'premium'")


def get_bot_setting(db_path, key, default=None):
    with _conn(db_path) as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def set_bot_setting(db_path, key, value):
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def insert_scan(db_path, scan):
    sql = """
    INSERT INTO scans (ts, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        credit, width, delta, iv_rank, dte, traded, trade_type)
    VALUES (:ts, :underlying, :strategy, :expiration,
        :short_put_strike, :long_put_strike, :short_call_strike, :long_call_strike,
        :credit, :width, :delta, :iv_rank, :dte, :traded, :trade_type)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, {'trade_type': 'premium', **scan}).lastrowid


def insert_trade(db_path, trade):
    sql = """
    INSERT INTO trades (scan_id, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        entry_credit, entry_ts, contracts, order_id, status, trade_type)
    VALUES (:scan_id, :underlying, :strategy, :expiration,
        :short_put_strike, :long_put_strike, :short_call_strike, :long_call_strike,
        :entry_credit, :entry_ts, :contracts, :order_id, 'open', :trade_type)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, {'trade_type': 'premium', **trade}).lastrowid


def update_trade_closed(db_path, trade_id, close_credit, close_reason, close_ts):
    sql = """UPDATE trades
             SET close_credit=?, close_ts=?, close_reason=?, status='closed'
             WHERE id=?"""
    with _conn(db_path) as conn:
        cursor = conn.execute(sql, (close_credit, close_ts, close_reason, trade_id))
        if cursor.rowcount == 0:
            raise ValueError(f"No trade found with id={trade_id}")


def update_trade_mark(db_path, trade_id, current_mark):
    with _conn(db_path) as conn:
        conn.execute("UPDATE trades SET current_mark=? WHERE id=?", (current_mark, trade_id))


def update_trade_status(db_path, trade_id, status):
    with _conn(db_path) as conn:
        cursor = conn.execute("UPDATE trades SET status=? WHERE id=?", (status, trade_id))
        if cursor.rowcount == 0:
            raise ValueError(f"No trade found with id={trade_id}")


def update_trade_order_id(db_path, trade_id, order_id):
    with _conn(db_path) as conn:
        cursor = conn.execute("UPDATE trades SET order_id=? WHERE id=?", (order_id, trade_id))
        if cursor.rowcount == 0:
            raise ValueError(f"No trade found with id={trade_id}")


def mark_scan_traded(db_path, scan_id):
    with _conn(db_path) as conn:
        cursor = conn.execute("UPDATE scans SET traded=1 WHERE id=?", (scan_id,))
        if cursor.rowcount == 0:
            raise ValueError(f"No scan found with id={scan_id}")


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
