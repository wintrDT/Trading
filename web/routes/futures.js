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
        "SELECT id, symbol, strategy, direction, entry_price, close_price, pnl, close_reason, close_ts FROM futures_trades WHERE status='closed' AND close_ts IS NOT NULL AND pnl IS NOT NULL AND close_ts > datetime('now','-3 months') ORDER BY close_ts DESC"
      ).all();
      for (const r of tradeRows) {
        const etDate = new Date(r.close_ts).toLocaleDateString('en-CA', {timeZone:'America/New_York'});
        if (!dailyStats[etDate]) dailyStats[etDate] = {wins:0, losses:0, win_total:0, loss_total:0, net:0, trades:[]};
        const pnl = parseFloat(r.pnl);
        dailyStats[etDate].net += pnl;
        if (pnl > 0) { dailyStats[etDate].wins++; dailyStats[etDate].win_total += pnl; }
        else if (pnl < 0) { dailyStats[etDate].losses++; dailyStats[etDate].loss_total += pnl; }
        dailyStats[etDate].trades.push({
          id: r.id, symbol: r.symbol, strategy: r.strategy, direction: r.direction,
          entry: parseFloat(r.entry_price),
          exit:  r.close_price != null ? parseFloat(r.close_price) : null,
          pnl:   pnl,
          reason: r.close_reason || null,
          ts: r.close_ts,
        });
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
  let recentFills = [];
  let backtest = null;
  if (botOnline) {
    const db2 = getDb();
    if (db2) {
      try {
        const row = db2.prepare("SELECT value FROM futures_settings WHERE key='market_status'").get();
        if (row) marketStatus = JSON.parse(row.value);
        const pauseRow = db2.prepare("SELECT value FROM futures_settings WHERE key='trading_paused'").get();
        if (pauseRow) tradingPaused = pauseRow.value === 'true';
        const fillsRow = db2.prepare("SELECT value FROM futures_settings WHERE key='recent_fills'").get();
        if (fillsRow) { try { recentFills = JSON.parse(fillsRow.value) || []; } catch (_) {} }
        const btRow = db2.prepare("SELECT value FROM futures_settings WHERE key='backtest_results'").get();
        if (btRow) { try { backtest = JSON.parse(btRow.value); } catch (_) {} }
      } catch (_) {}
      finally { db2.close(); }
    }
  }

  // Prefer the latest broker-reported balance from the snapshot job (TopStep when
  // connected, otherwise sim-derived). Fall back to the legacy 500+all-time-P&L
  // calculation if no snapshot exists yet.
  const snapBalance = accountSnap?.net_liq != null ? parseFloat(accountSnap.net_liq) : null;
  const netLiq = !botOnline ? null
    : (snapBalance != null && snapBalance !== 500)
      ? snapBalance
      : parseFloat((500 + allTimePnl).toFixed(2));

  res.render('futures', {
    user: req.session.user,
    openTrades,
    closedTrades,
    recentSignals,
    recentNews,
    accountSnap,
    botOnline,
    marketStatus,
    recentFills,
    backtest,
    netLiq,
    tradingPaused,
    todayPnl:   botOnline ? todayPnl   : null,
    allTimePnl: botOnline ? allTimePnl : null,
    dailyStats,
  });
});

// Live data for in-place updates — no full page reload
router.get('/api/live', requireAuth, (req, res) => {
  const db = getDb();
  if (!db) return res.status(503).json({ ok: false });
  try {
    let marketStatus = {};
    try {
      const row = db.prepare("SELECT value FROM futures_settings WHERE key='market_status'").get();
      if (row) marketStatus = JSON.parse(row.value);
    } catch (_) {}

    const opens = db.prepare(
      "SELECT id, symbol, direction, contracts, entry_price, current_price FROM futures_trades WHERE status='open'"
    ).all();
    const openTrades = opens.map(t => {
      const pv      = t.symbol === 'NQ' ? 20 : 50;
      const curr    = t.current_price != null ? parseFloat(t.current_price) : null;
      const entry   = parseFloat(t.entry_price);
      const unreal  = curr != null ? (t.direction === 'long' ? curr - entry : entry - curr) * t.contracts * pv : null;
      return { id: t.id, current: curr, unrealized: unreal != null ? Math.round(unreal * 100) / 100 : null };
    });

    const today = new Date().toISOString().slice(0, 10);
    const todayRow = db.prepare(
      "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed' AND close_ts LIKE ?"
    ).get(`${today}%`);
    const allRow = db.prepare(
      "SELECT COALESCE(SUM(pnl),0) as total FROM futures_trades WHERE status='closed'"
    ).get();
    const todayPnl   = todayRow ? parseFloat(todayRow.total) : 0;
    const allTimePnl = allRow   ? parseFloat(allRow.total)   : 0;

    res.json({ ok: true, marketStatus, openTrades, todayPnl, allTimePnl, netLiq: parseFloat((500 + allTimePnl).toFixed(2)) });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  } finally {
    db.close();
  }
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

// Close an open futures position. For LIVE trades (order_id != 'SIM') this
// FIRST closes the real position at TopStep, then marks the DB closed. If the
// broker close fails, the DB is NOT updated — prevents the orphaned-position
// bug where the bot thinks it's flat but TopStep still holds the contracts.
router.post('/close/:id', requireAuth, async (req, res) => {
  const axios = require('axios');
  const db = getDb(false);
  if (!db) return res.status(503).json({ error: 'DB unavailable' });

  try {
    const trade = db.prepare("SELECT * FROM futures_trades WHERE id=? AND status='open'").get(req.params.id);
    if (!trade) return res.status(404).json({ error: 'Trade not found or already closed' });

    const isLive = trade.order_id && trade.order_id !== 'SIM' && trade.order_id !== 'UNKNOWN';

    // For live trades, close the real TopStep position FIRST
    if (isLive) {
      const getFs = (k) => {
        try { const r = db.prepare("SELECT value FROM futures_settings WHERE key=?").get(k); return r ? r.value : ''; }
        catch { return ''; }
      };
      const tsUser = getFs('topstep_username');
      const tsKey  = getFs('topstep_api_key');
      const tsAcct = parseInt(getFs('topstep_account_id'), 10);
      if (!tsUser || !tsKey || !tsAcct) {
        return res.status(500).json({ error: 'Live trade but TopStep credentials missing — cannot close at broker' });
      }
      try {
        const BASE = 'https://api.topstepx.com';
        const auth = await axios.post(`${BASE}/api/Auth/loginKey`, { userName: tsUser, apiKey: tsKey }, { timeout: 12000 });
        const token = auth.data?.token || auth.data?.accessToken;
        if (!token) return res.status(502).json({ error: 'TopStep auth failed during close' });
        const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

        // Find the actual open position for this symbol (most robust — close exactly what's open)
        const posResp = await axios.post(`${BASE}/api/Position/searchOpen`, { accountId: tsAcct }, { headers, timeout: 10000 });
        const positions = posResp.data?.positions || posResp.data || [];
        const match = positions.find(p => (p.contractDisplayName || '').toUpperCase().startsWith(trade.symbol.toUpperCase()));
        if (!match) {
          // Nothing open at TopStep for this symbol — broker is already flat; just sync the DB
          console.warn(`[futures] close: no TopStep position for ${trade.symbol}, syncing DB only`);
        } else {
          const closeResp = await axios.post(`${BASE}/api/Position/closeContract`,
            { accountId: tsAcct, contractId: match.contractId }, { headers, timeout: 10000 });
          if (!closeResp.data?.success) {
            return res.status(502).json({ error: `TopStep close failed: ${closeResp.data?.errorMessage || 'unknown'}` });
          }
        }
      } catch (e) {
        const msg = e.response?.data?.errorMessage || e.response?.data?.error || e.message;
        console.error('[futures] TopStep close error:', msg);
        // DO NOT mark closed in DB — position may still be open at broker
        return res.status(502).json({ error: `Broker close failed (position may still be open): ${msg}` });
      }
    }

    // Broker close succeeded (or was sim/already-flat) — now update the DB
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

    res.json({ ok: true, price: currentPrice, pnl: pnlRounded, broker: isLive ? 'TopStep closed' : 'sim' });
  } catch (err) {
    console.error('[futures] close error:', err.message);
    res.status(500).json({ error: err.message });
  } finally {
    db.close();
  }
});

module.exports = router;
