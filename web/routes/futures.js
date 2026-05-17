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
  let recentNews    = [];
  let accountSnap   = null;
  let allTimePnl    = null;
  let todayPnl      = null;
  let botOnline     = false;
  let dailyStats    = {};

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

      // Daily wins/losses for calendar — bucket by ET date so overnight trades land on the right day
      const tradeRows = db.prepare(
        "SELECT close_ts, pnl FROM futures_trades WHERE status='closed' AND close_ts IS NOT NULL AND pnl IS NOT NULL AND close_ts > datetime('now','-3 months')"
      ).all();
      for (const r of tradeRows) {
        const etDate = new Date(r.close_ts).toLocaleDateString('en-CA', {timeZone:'America/New_York'});
        if (!dailyStats[etDate]) dailyStats[etDate] = {wins:0, losses:0, win_total:0, loss_total:0, net:0};
        const pnl = parseFloat(r.pnl);
        dailyStats[etDate].net += pnl;
        if (pnl > 0) { dailyStats[etDate].wins++; dailyStats[etDate].win_total += pnl; }
        else if (pnl < 0) { dailyStats[etDate].losses++; dailyStats[etDate].loss_total += pnl; }
      }

      try {
        recentNews = db.prepare(
          "SELECT * FROM futures_news ORDER BY event_ts DESC LIMIT 15"
        ).all();
      } catch (_) {
        recentNews = [];
      }

      botOnline = true;
    } catch (err) {
      console.error('[futures] DB query error:', err.message);
    } finally {
      db.close();
    }
  }

  let marketStatus = null;
  let tradingPaused = false;
  if (botOnline) {
    const db2 = getDb();
    if (db2) {
      try {
        const row = db2.prepare("SELECT value FROM futures_settings WHERE key='market_status'").get();
        if (row) marketStatus = JSON.parse(row.value);
        const pauseRow = db2.prepare("SELECT value FROM futures_settings WHERE key='trading_paused'").get();
        if (pauseRow) tradingPaused = pauseRow.value === 'true';
      } catch (_) {}
      finally { db2.close(); }
    }
  }

  const netLiq = botOnline ? parseFloat((500 + allTimePnl).toFixed(2)) : null;

  res.render('futures', {
    user: req.session.user,
    openTrades,
    closedTrades,
    recentSignals,
    recentNews,
    accountSnap,
    botOnline,
    marketStatus,
    netLiq,
    tradingPaused,
    todayPnl:   botOnline ? todayPnl   : null,
    allTimePnl: botOnline ? allTimePnl : null,
    dailyStats,
  });
});

router.post('/toggle-trading', requireAuth, (req, res) => {
  const db = getDb(false);
  if (!db) return res.status(503).json({ error: 'DB unavailable' });
  try {
    const row = db.prepare("SELECT value FROM futures_settings WHERE key='trading_paused'").get();
    const current = row ? row.value === 'true' : false;
    const next = !current;
    db.prepare("INSERT INTO futures_settings(key,value) VALUES('trading_paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(next ? 'true' : 'false');
    res.json({ ok: true, paused: next });
  } catch (err) {
    res.status(500).json({ error: err.message });
  } finally {
    db.close();
  }
});

router.post('/close/:id', requireAuth, (req, res) => {
  const db = getDb(false);
  if (!db) return res.status(503).json({ error: 'DB unavailable' });

  try {
    const trade = db.prepare("SELECT * FROM futures_trades WHERE id=? AND status='open'").get(req.params.id);
    if (!trade) return res.status(404).json({ error: 'Trade not found or already closed' });

    // Get current price from latest market_status
    let currentPrice = null;
    try {
      const row = db.prepare("SELECT value FROM futures_settings WHERE key='market_status'").get();
      if (row) {
        const status = JSON.parse(row.value);
        const sym = status[trade.symbol];
        if (sym && sym.price) currentPrice = sym.price;
      }
    } catch (_) {}

    if (!currentPrice) currentPrice = trade.current_price || trade.entry_price;

    const pointValue = trade.symbol === 'NQ' ? 20 : 50;
    const pnl = (trade.direction === 'long'
      ? currentPrice - trade.entry_price
      : trade.entry_price - currentPrice) * trade.contracts * pointValue;
    const pnlRounded = Math.round(pnl * 100) / 100;
    const closeTs = new Date().toISOString();

    const result = db.prepare(
      "UPDATE futures_trades SET status='closed', close_price=?, close_reason='manual_close', close_ts=?, pnl=? WHERE id=? AND status='open'"
    ).run(currentPrice, closeTs, pnlRounded, trade.id);

    if (result.changes === 0) {
      return res.status(409).json({ error: 'Trade already closed or DB conflict — try again' });
    }

    res.json({ ok: true, price: currentPrice, pnl: pnlRounded });
  } catch (err) {
    console.error('[futures] close error:', err.message);
    res.status(500).json({ error: err.message });
  } finally {
    db.close();
  }
});

module.exports = router;
