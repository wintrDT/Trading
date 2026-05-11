const router = require('express').Router();
const path = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');

const DB_PATH = path.join(__dirname, '../../bot/data/options.db');

function getDb() {
  try {
    return new Database(DB_PATH, { readonly: true, fileMustExist: true });
  } catch (err) {
    if (err.code !== 'SQLITE_CANTOPEN') {
      console.error('[options] DB open error:', err.message);
    }
    return null;
  }
}

router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  let openTrades = [];
  let closedTrades = [];
  let recentScans = [];
  let accountSnap = null;
  let allTimePnl = null;
  let botOnline = false;

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
      const allClosedTrades = db.prepare(
        "SELECT entry_credit, close_credit, contracts FROM trades WHERE status='closed' AND close_credit IS NOT NULL"
      ).all();
      allTimePnl = allClosedTrades.reduce((sum, t) => {
        return sum + (parseFloat(t.entry_credit) - parseFloat(t.close_credit)) * t.contracts * 100;
      }, 0);
      botOnline = true;
    } catch (err) {
      console.error('[options] DB query error:', err.message);
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
    botOnline,
    allTimePnl: botOnline ? allTimePnl : null,
  });
});

module.exports = router;
