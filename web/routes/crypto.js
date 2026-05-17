// web/routes/crypto.js
const router   = require('express').Router();
const path     = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');

const DB_PATH = path.join(__dirname, '../../bot/data/crypto.db');

function getDb(readonly = true) {
  try {
    return new Database(DB_PATH, { readonly, fileMustExist: true });
  } catch (err) {
    if (err.code !== 'SQLITE_CANTOPEN') {
      console.error('[crypto] DB open error:', err.message);
    }
    return null;
  }
}

const DISPLAY = { BTCUSDT: 'BTC', ETHUSDT: 'ETH', SOLUSDT: 'SOL', DOGEUSDT: 'DOGE' };

router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  let openTrades = [], closedTrades = [], recentSignals = [];
  let snap = null, todayPnl = null, allTimePnl = null, botOnline = false;
  let winRate = null, winRateTotal = 0, livePrices = {};

  if (db) {
    try {
      openTrades = db.prepare(
        "SELECT * FROM crypto_trades WHERE status='open' ORDER BY entry_ts DESC"
      ).all();
      closedTrades = db.prepare(
        "SELECT * FROM crypto_trades WHERE status='closed' ORDER BY close_ts DESC LIMIT 50"
      ).all();
      recentSignals = db.prepare(
        "SELECT * FROM crypto_signals ORDER BY id DESC LIMIT 20"
      ).all();
      snap = db.prepare(
        "SELECT * FROM crypto_snapshots ORDER BY id DESC LIMIT 1"
      ).get() || null;

      const today = new Date().toISOString().slice(0, 10);
      const todayRow = db.prepare(
        "SELECT COALESCE(SUM(pnl),0) as total FROM crypto_trades WHERE status='closed' AND close_ts LIKE ?"
      ).get(`${today}%`);
      todayPnl = todayRow ? parseFloat(todayRow.total) : 0;

      const allRow = db.prepare(
        "SELECT COALESCE(SUM(pnl),0) as total FROM crypto_trades WHERE status='closed'"
      ).get();
      allTimePnl = allRow ? parseFloat(allRow.total) : 0;

      const statsRow = db.prepare(
        "SELECT COUNT(*) as total, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins FROM crypto_trades WHERE status='closed'"
      ).get();
      if (statsRow && statsRow.total > 0) {
        winRate      = Math.round((statsRow.wins / statsRow.total) * 100);
        winRateTotal = statsRow.total;
      }

      const priceRows = db.prepare(
        "SELECT key, value FROM crypto_settings WHERE key LIKE 'price_%'"
      ).all();
      for (const row of priceRows) {
        livePrices[row.key.slice(6)] = parseFloat(row.value);
      }

      botOnline = true;
    } catch (err) {
      console.error('[crypto] DB query error:', err.message);
    } finally {
      db.close();
    }
  }

  res.render('crypto', {
    user: req.session.user,
    DISPLAY,
    openTrades, closedTrades, recentSignals,
    snap, botOnline, livePrices,
    winRate, winRateTotal,
    todayPnl:   botOnline ? todayPnl   : null,
    allTimePnl: botOnline ? allTimePnl : null,
  });
});

router.post('/close/:id', requireAuth, (req, res) => {
  const db = getDb(false);
  if (!db) return res.status(503).json({ error: 'DB unavailable' });

  try {
    const trade = db.prepare("SELECT * FROM crypto_trades WHERE id=? AND status='open'").get(req.params.id);
    if (!trade) return res.status(404).json({ error: 'Trade not found or already closed' });

    let currentPrice = null;
    try {
      const row = db.prepare("SELECT value FROM crypto_settings WHERE key=?").get(`price_${trade.symbol}`);
      if (row) currentPrice = parseFloat(row.value);
    } catch (_) {}
    if (!currentPrice) currentPrice = parseFloat(trade.current_price || trade.entry_price);

    const size  = parseFloat(trade.size);
    const entry = parseFloat(trade.entry_price);
    const pnl   = trade.direction === 'long'
      ? (currentPrice - entry) * size
      : (entry - currentPrice) * size;
    const pnlRounded = Math.round(pnl * 100) / 100;
    const closeTs    = new Date().toISOString();

    const result = db.prepare(
      "UPDATE crypto_trades SET status='closed', close_price=?, close_reason='manual_close', close_ts=?, pnl=? WHERE id=? AND status='open'"
    ).run(currentPrice, closeTs, pnlRounded, trade.id);

    if (result.changes === 0) {
      return res.status(409).json({ error: 'Trade already closed or DB conflict — try again' });
    }
    res.json({ ok: true, price: currentPrice, pnl: pnlRounded });
  } catch (err) {
    console.error('[crypto] close error:', err.message);
    res.status(500).json({ error: err.message });
  } finally {
    db.close();
  }
});

module.exports = router;
