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

  res.render('futures', {
    user: req.session.user,
    openTrades,
    closedTrades,
    recentSignals,
    recentNews,
    accountSnap,
    botOnline,
    todayPnl:   botOnline ? todayPnl   : null,
    allTimePnl: botOnline ? allTimePnl : null,
  });
});

module.exports = router;
