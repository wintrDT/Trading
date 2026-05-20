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
    pnl REAL,
    entry_rsi REAL,
    entry_dev_pct REAL,
    max_favorable REAL,
    max_adverse REAL
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

_CREATE_NEWS = """
CREATE TABLE IF NOT EXISTS futures_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_ts TEXT,
    news_type TEXT,
    title TEXT,
    event_ts TEXT,
    impact TEXT,
    sentiment TEXT,
    url TEXT
)
"""


@contextlib.contextmanager
def _conn(db_path):
    # timeout=10s + WAL + busy_timeout so the Python bot (writes every 5s) and the
    # Node web server (reads + manual closes) don't hit 'database is locked' under
    # concurrent access. WAL lets readers and writers work without blocking.
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')
    except Exception:
        pass
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
        trade_cols = {r[1] for r in conn.execute("PRAGMA table_info(futures_trades)").fetchall()}
        if 'entry_rsi' not in trade_cols:
            conn.execute("ALTER TABLE futures_trades ADD COLUMN entry_rsi REAL")
        if 'entry_dev_pct' not in trade_cols:
            conn.execute("ALTER TABLE futures_trades ADD COLUMN entry_dev_pct REAL")
        if 'max_favorable' not in trade_cols:
            conn.execute("ALTER TABLE futures_trades ADD COLUMN max_favorable REAL")
        if 'max_adverse' not in trade_cols:
            conn.execute("ALTER TABLE futures_trades ADD COLUMN max_adverse REAL")
        if 'stop_order_id' not in trade_cols:
            conn.execute("ALTER TABLE futures_trades ADD COLUMN stop_order_id TEXT")


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
        stop_price, target_price, contracts, order_id, status, entry_rsi, entry_dev_pct, stop_order_id)
    VALUES (:symbol, :strategy, :direction, :entry_price, :entry_ts,
        :stop_price, :target_price, :contracts, :order_id, :status, :entry_rsi, :entry_dev_pct, :stop_order_id)
    """
    with _conn(db_path) as conn:
        return conn.execute(sql, {'entry_rsi': None, 'entry_dev_pct': None, 'stop_order_id': None, **trade}).lastrowid


def update_trade_price(db_path, trade_id, current_price, new_stop=None):
    """Update current_price (always) and optionally stop_price (when trailing stop moves).

    The new_stop param was missing for the bot's lifetime — every trailing-stop
    computation in manager.py was being thrown away on the next iteration because
    the new stop was only ever assigned to a local variable, never persisted.
    """
    with _conn(db_path) as conn:
        if new_stop is not None:
            cur = conn.execute(
                "UPDATE futures_trades SET current_price=?, stop_price=? WHERE id=?",
                (current_price, new_stop, trade_id),
            )
        else:
            cur = conn.execute(
                "UPDATE futures_trades SET current_price=? WHERE id=?",
                (current_price, trade_id),
            )
        if cur.rowcount == 0:
            raise ValueError(f"No futures trade with id={trade_id}")


def update_trade_extremes(db_path, trade_id, max_fav, max_adv):
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE futures_trades SET max_favorable=?, max_adverse=? WHERE id=?",
            (max_fav, max_adv, trade_id),
        )


def update_trade_closed(db_path, trade_id, close_price, close_reason, close_ts, pnl):
    with _conn(db_path) as conn:
        # AND status='open' guard prevents double-close race (manager + web button)
        # from overwriting close data and double-counting P&L.
        cur = conn.execute(
            "UPDATE futures_trades SET close_price=?,close_ts=?,close_reason=?,status='closed',pnl=? "
            "WHERE id=? AND status='open'",
            (close_price, close_ts, close_reason, pnl, trade_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No OPEN futures trade with id={trade_id} (already closed?)")


def mark_signal_traded(db_path, signal_id):
    with _conn(db_path) as conn:
        cur = conn.execute("UPDATE futures_signals SET traded=1 WHERE id=?", (signal_id,))
        if cur.rowcount == 0:
            raise ValueError(f"No futures signal with id={signal_id}")


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
    """DEPRECATED string-prefix version — kept for callers that pass an ET date
    but it mismatches UTC-stored close_ts after ~8 PM ET. Prefer
    get_daily_pnl_range() with explicit UTC bounds."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed' AND close_ts LIKE ?",
            (f"{date_str}%",)
        ).fetchone()
        return float(row['total'])


def get_daily_pnl_range(db_path, start_utc_iso, end_utc_iso):
    """Sum P&L for trades closed within a UTC datetime range [start, end).
    Use this with the UTC bounds of the ET trading day so evening trades
    (which roll to the next UTC date) are correctly counted — fixes the
    daily-loss-limit undercount bug."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades "
            "WHERE status='closed' AND close_ts >= ? AND close_ts < ?",
            (start_utc_iso, end_utc_iso)
        ).fetchone()
        return float(row['total'])


def get_all_time_pnl(db_path):
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed'"
        ).fetchone()
        return float(row['total'])


def upsert_news(db_path, items: list):
    """Replace today's news records with fresh fetch."""
    if not items:
        return
    today = items[0]['fetched_ts'][:10]
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


_SENTIMENT_SCORES = {
    'Bullish': 1.0, 'Somewhat-Bullish': 0.5, 'Neutral': 0.0,
    'Somewhat-Bearish': -0.5, 'Bearish': -1.0,
}

def get_market_bias(db_path: str, symbol: str = None) -> str | None:
    """Returns 'long', 'short', or None based on recent news sentiment.
    When symbol is provided, re-scores headlines using symbol-specific keywords."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT sentiment, title FROM futures_news WHERE news_type='headline' AND sentiment IS NOT NULL "
            "ORDER BY fetched_ts DESC LIMIT 10"
        ).fetchall()
    if not rows:
        return None

    if symbol:
        from bot.futures.config import SYMBOL_NEWS_KEYWORDS
        kw = SYMBOL_NEWS_KEYWORDS.get(symbol)
        if kw:
            scores = []
            for r in rows:
                title = (r['title'] or '').lower()
                bull = sum(1 for w in kw['bull'] if w in title)
                bear = sum(1 for w in kw['bear'] if w in title)
                if bull > bear:
                    scores.append(1.0)
                elif bear > bull:
                    scores.append(-1.0)
                else:
                    scores.append(_SENTIMENT_SCORES.get(r['sentiment'], 0.0))
            avg = sum(scores) / len(scores)
            if avg >= 0.2:  return 'long'
            if avg <= -0.2: return 'short'
            return None

    scores = [_SENTIMENT_SCORES.get(r['sentiment'], 0.0) for r in rows]
    avg = sum(scores) / len(scores)
    if avg >= 0.2:
        return 'long'
    if avg <= -0.2:
        return 'short'
    return None


def get_last_close_ts(db_path, symbol: str):
    """Returns ISO close timestamp of the most recent closed trade for symbol, or None."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT close_ts FROM futures_trades WHERE symbol=? AND status='closed' ORDER BY close_ts DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        return row['close_ts'] if row else None


def get_last_close_info(db_path, symbol: str):
    """Returns (close_ts, close_reason) of the most recent closed trade for symbol, or (None, None)."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT close_ts, close_reason FROM futures_trades WHERE symbol=? AND status='closed' "
            "ORDER BY close_ts DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        return (row['close_ts'], row['close_reason']) if row else (None, None)


def get_today_event_times(db_path, date_str: str) -> list:
    """Return ISO datetimes of high-impact economic events today."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT event_ts FROM futures_news WHERE news_type='event' AND impact='High' AND event_ts LIKE ?",
            (f"{date_str}%",)
        ).fetchall()
        return [r['event_ts'] for r in rows]
