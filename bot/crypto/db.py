# bot/crypto/db.py
import contextlib
import sqlite3

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS crypto_trades (
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
    size REAL DEFAULT 1.0,
    status TEXT DEFAULT 'open',
    pnl REAL
)
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS crypto_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    symbol TEXT,
    strategy TEXT,
    direction TEXT,
    price REAL,
    traded INTEGER DEFAULT 0
)
"""

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS crypto_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    equity REAL,
    realized_pnl_today REAL
)
"""

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS crypto_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""

_CREATE_NEWS = """
CREATE TABLE IF NOT EXISTS crypto_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_ts TEXT,
    symbol TEXT,
    title TEXT,
    published_at TEXT,
    sentiment TEXT,
    score REAL,
    url TEXT
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
        conn.execute(_CREATE_NEWS)


def get_setting(db_path, key, default=None):
    with _conn(db_path) as conn:
        row = conn.execute("SELECT value FROM crypto_settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def set_setting(db_path, key, value):
    with _conn(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO crypto_settings (key, value) VALUES (?, ?)", (key, value))


def get_latest_prices(db_path):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM crypto_settings WHERE key LIKE 'price_%'"
        ).fetchall()
        return {row['key'][6:]: float(row['value']) for row in rows}


def insert_signal(db_path, sig):
    sql = """
    INSERT INTO crypto_signals (ts, symbol, strategy, direction, price, traded)
    VALUES (:ts, :symbol, :strategy, :direction, :price, :traded)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, sig).lastrowid


def insert_trade(db_path, trade):
    sql = """
    INSERT INTO crypto_trades (symbol, strategy, direction, entry_price, entry_ts,
        stop_price, target_price, size, status)
    VALUES (:symbol, :strategy, :direction, :entry_price, :entry_ts,
        :stop_price, :target_price, :size, :status)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, trade).lastrowid


def update_trade_price(db_path, trade_id, current_price, new_stop=None):
    with _conn(db_path) as conn:
        if new_stop is not None:
            cur = conn.execute(
                "UPDATE crypto_trades SET current_price=?, stop_price=? WHERE id=?",
                (current_price, new_stop, trade_id),
            )
        else:
            cur = conn.execute(
                "UPDATE crypto_trades SET current_price=? WHERE id=?",
                (current_price, trade_id),
            )
        if cur.rowcount == 0:
            raise ValueError(f"No crypto trade with id={trade_id}")


def update_trade_closed(db_path, trade_id, close_price, close_reason, close_ts, pnl):
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE crypto_trades SET close_price=?,close_ts=?,close_reason=?,status='closed',pnl=? WHERE id=?",
            (close_price, close_ts, close_reason, pnl, trade_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No crypto trade with id={trade_id}")


def mark_signal_traded(db_path, signal_id):
    with _conn(db_path) as conn:
        cur = conn.execute("UPDATE crypto_signals SET traded=1 WHERE id=?", (signal_id,))
        if cur.rowcount == 0:
            raise ValueError(f"No crypto signal with id={signal_id}")


def get_open_trades(db_path):
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM crypto_trades WHERE status='open' ORDER BY entry_ts DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_pnl(db_path, date_str):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM crypto_trades WHERE status='closed' AND close_ts LIKE ?",
            (f"{date_str}%",)
        ).fetchone()
        return float(row['total'])


def get_all_time_pnl(db_path):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM crypto_trades WHERE status='closed'"
        ).fetchone()
        return float(row['total'])


def insert_snapshot(db_path, snap):
    sql = """
    INSERT INTO crypto_snapshots (ts, equity, realized_pnl_today)
    VALUES (:ts, :equity, :realized_pnl_today)
    """
    with _conn(db_path) as conn:
        conn.execute(sql, snap)


def upsert_crypto_news(db_path, items: list):
    """Replace last 6 hours of news with fresh fetch."""
    if not items:
        return
    cutoff = items[0]['fetched_ts'][:13]  # keep rows older than current hour
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM crypto_news WHERE fetched_ts LIKE ?", (f"{cutoff}%",))
        for item in items:
            conn.execute(
                "INSERT INTO crypto_news (fetched_ts, symbol, title, published_at, sentiment, score, url) "
                "VALUES (:fetched_ts, :symbol, :title, :published_at, :sentiment, :score, :url)",
                item,
            )


_SENTIMENT_SCORES = {'Bullish': 1.0, 'Neutral': 0.0, 'Bearish': -1.0}


def get_crypto_bias(db_path, symbol: str) -> str | None:
    """Return 'long', 'short', or None based on recent CryptoPanic sentiment for this symbol."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT sentiment FROM crypto_news WHERE symbol=? OR symbol='ALL' "
            "ORDER BY fetched_ts DESC LIMIT 10",
            (symbol,)
        ).fetchall()
    if not rows:
        return None
    vals = [_SENTIMENT_SCORES.get(r['sentiment'], 0.0) for r in rows]
    avg  = sum(vals) / len(vals)
    if avg >= 0.3:
        return 'long'
    if avg <= -0.3:
        return 'short'
    return None
