const router = require('express').Router();
const path = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');

const DB_PATH = path.join(__dirname, '../../bot/data/options.db');

function getDb(readonly = true) {
  try {
    return new Database(DB_PATH, { readonly, fileMustExist: true });
  } catch (err) {
    if (err.code !== 'SQLITE_CANTOPEN') {
      console.error('[options] DB open error:', err.message);
    }
    return null;
  }
}

function isSimMode() {
  const db = getDb();
  if (!db) return false;
  try {
    const row = db.prepare("SELECT value FROM bot_settings WHERE key='trading_mode'").get();
    return row && row.value === 'sim';
  } catch { return false; }
  finally { db.close(); }
}

router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  let openTrades = [];
  let openTrades0dte = [];
  let closedTrades = [];
  let closedTrades0dte = [];
  let recentScans = [];
  let recentScans0dte = [];
  let accountSnap = null;
  let allTimePnl = null;
  let botOnline = false;

  if (db) {
    try {
      const allOpen = db.prepare(
        "SELECT * FROM trades WHERE status IN ('open','pending') ORDER BY entry_ts DESC"
      ).all();
      openTrades     = allOpen.filter(t => (t.trade_type || 'premium') !== '0dte');
      openTrades0dte = allOpen.filter(t => t.trade_type === '0dte');

      const allClosed = db.prepare(
        "SELECT * FROM trades WHERE status='closed' ORDER BY close_ts DESC LIMIT 100"
      ).all();
      closedTrades     = allClosed.filter(t => (t.trade_type || 'premium') !== '0dte').slice(0, 50);
      closedTrades0dte = allClosed.filter(t => t.trade_type === '0dte').slice(0, 50);

      const allScans = db.prepare(
        "SELECT * FROM scans ORDER BY id DESC LIMIT 40"
      ).all();
      recentScans     = allScans.filter(s => (s.trade_type || 'premium') !== '0dte').slice(0, 20);
      recentScans0dte = allScans.filter(s => s.trade_type === '0dte').slice(0, 20);

      accountSnap = db.prepare(
        "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1"
      ).get() || null;
      const allClosedForPnl = db.prepare(
        "SELECT entry_credit, close_credit, contracts FROM trades WHERE status='closed' AND close_credit IS NOT NULL"
      ).all();
      allTimePnl = allClosedForPnl.reduce((sum, t) => {
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
    openTrades0dte,
    closedTrades,
    closedTrades0dte,
    recentScans,
    recentScans0dte,
    accountSnap,
    botOnline,
    allTimePnl: botOnline ? allTimePnl : null,
    simMode: isSimMode(),
  });
});

router.post('/manual-trade', requireAuth, (req, res) => {
  if (!isSimMode()) return res.status(403).json({ error: 'Only available in Simulation mode' });

  const { underlying, strategy, expiration,
          short_put, long_put, short_call, long_call, credit, contracts,
          trade_type = 'premium' } = req.body;

  if (!underlying || !strategy || !expiration || !short_put || !long_put || !credit) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const isIC = strategy === 'iron_condor';
  if (isIC && (!short_call || !long_call)) {
    return res.status(400).json({ error: 'Iron condor requires call strikes' });
  }

  const db = getDb(false);
  if (!db) return res.status(500).json({ error: 'DB unavailable' });

  try {
    const now = new Date().toISOString();
    const scanId = db.prepare(`
      INSERT INTO scans (ts, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        credit, width, delta, iv_rank, dte, traded, trade_type)
      VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0,1,?)
    `).run(now, underlying, strategy, expiration,
           parseFloat(short_put), parseFloat(long_put),
           isIC ? parseFloat(short_call) : null,
           isIC ? parseFloat(long_call) : null,
           parseFloat(credit),
           parseFloat(short_put) - parseFloat(long_put),
           trade_type).lastInsertRowid;

    db.prepare(`
      INSERT INTO trades (scan_id, underlying, strategy, expiration,
        short_put_strike, long_put_strike, short_call_strike, long_call_strike,
        entry_credit, entry_ts, contracts, order_id, status, trade_type)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,'SIM','open',?)
    `).run(scanId, underlying, strategy, expiration,
           parseFloat(short_put), parseFloat(long_put),
           isIC ? parseFloat(short_call) : null,
           isIC ? parseFloat(long_call) : null,
           parseFloat(credit), now,
           Math.max(1, parseInt(contracts) || 1),
           trade_type);

    res.json({ ok: true });
  } catch (err) {
    console.error('[options] manual-trade error:', err.message);
    res.status(500).json({ error: err.message });
  } finally {
    db.close();
  }
});

module.exports = router;
