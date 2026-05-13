// web/routes/settings.js
const router = require('express').Router();
const axios  = require('axios');
const path   = require('path');
const Database = require('better-sqlite3');
const { requireAuth } = require('../middleware');
const db     = require('../db');
const { encrypt, decrypt } = require('../../utils/encrypt');

const BOT_DB_PATH     = path.join(__dirname, '../../bot/data/options.db');
const FUTURES_DB_PATH = path.join(__dirname, '../../bot/data/futures.db');

function getBotDb() {
  try { return new Database(BOT_DB_PATH, { fileMustExist: true }); } catch { return null; }
}
function getBotSetting(key, def = 'live') {
  const bdb = getBotDb();
  if (!bdb) return def;
  try {
    const row = bdb.prepare('SELECT value FROM bot_settings WHERE key=?').get(key);
    return row ? row.value : def;
  } finally { bdb.close(); }
}
function setBotSetting(key, value) {
  const bdb = getBotDb();
  if (!bdb) return;
  try {
    bdb.prepare("INSERT INTO bot_settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(key, value);
  } finally { bdb.close(); }
}

function getFuturesDb() {
  try {
    const fdb = new Database(FUTURES_DB_PATH);
    fdb.prepare('CREATE TABLE IF NOT EXISTS futures_settings (key TEXT PRIMARY KEY, value TEXT)').run();
    return fdb;
  } catch { return null; }
}
function getFuturesSetting(key, def = '') {
  const fdb = getFuturesDb();
  if (!fdb) return def;
  try {
    const row = fdb.prepare('SELECT value FROM futures_settings WHERE key=?').get(key);
    return row ? row.value : def;
  } finally { fdb.close(); }
}
function setFuturesSetting(key, value) {
  const fdb = getFuturesDb();
  if (!fdb) return;
  try {
    fdb.prepare("INSERT INTO futures_settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(key, String(value));
  } finally { fdb.close(); }
}

const PRIVATE_URL = 'https://trading-api.kalshi.com/trade-api/v2';

router.get('/', requireAuth, (req, res) => {
  const row = db.prepare('SELECT kalshi_email, kalshi_password_enc, kalshi_api_key_enc FROM users WHERE id = ?').get(req.session.user.id);
  res.render('settings', {
    user:               req.session.user,
    kalshiEmail:        row?.kalshi_email || '',
    hasPassword:        !!(row?.kalshi_password_enc),
    hasApiKey:          !!(row?.kalshi_api_key_enc),
    tradingMode:        getBotSetting('trading_mode', 'live'),
    tvUsername:         getFuturesSetting('tv_username', ''),
    tvCid:              getFuturesSetting('tv_cid', ''),
    tvDeviceId:         getFuturesSetting('tv_device_id', 'sharp-bot-futures-001'),
    tvDemo:             getFuturesSetting('tv_demo', 'true'),
    hasTvPassword:      !!getFuturesSetting('tv_password', ''),
    hasTvSec:           !!getFuturesSetting('tv_sec', ''),
    futuresTradingMode: getFuturesSetting('trading_mode', 'sim'),
    saved:              req.query.saved === '1',
    error:              null,
  });
});

router.post('/trading-mode', requireAuth, (req, res) => {
  const mode = req.body.mode === 'sim' ? 'sim' : 'live';
  setBotSetting('trading_mode', mode);
  res.redirect('/settings?saved=1');
});

// Save email + password
router.post('/password', requireAuth, (req, res) => {
  const { kalshi_email, kalshi_password, kalshi_password_confirm } = req.body;
  const email = (kalshi_email || '').trim().toLowerCase();

  if (!email || !email.includes('@')) {
    return res.render('settings', {
      user: req.session.user, kalshiEmail: email, hasPassword: false, hasApiKey: false, saved: false,
      error: 'Enter a valid Kalshi email address.',
    });
  }

  if (!kalshi_password) {
    // Only update email, keep existing password
    db.prepare('UPDATE users SET kalshi_email = ? WHERE id = ?').run(email, req.session.user.id);
    return res.redirect('/settings?saved=1');
  }

  if (kalshi_password !== kalshi_password_confirm) {
    return res.render('settings', {
      user: req.session.user, kalshiEmail: email, hasPassword: false, hasApiKey: false, saved: false,
      error: 'Passwords do not match.',
    });
  }

  db.prepare('UPDATE users SET kalshi_email = ?, kalshi_password_enc = ? WHERE id = ?')
    .run(email, encrypt(kalshi_password), req.session.user.id);
  res.redirect('/settings?saved=1');
});

// Save API key
router.post('/apikey', requireAuth, (req, res) => {
  const key = (req.body.kalshi_api_key || '').trim();
  if (!key) {
    return res.redirect('/settings?saved=1'); // blank = no change
  }
  db.prepare('UPDATE users SET kalshi_api_key_enc = ? WHERE id = ?')
    .run(encrypt(key), req.session.user.id);
  res.redirect('/settings?saved=1');
});

// Test credentials live (AJAX)
router.post('/test', requireAuth, async (req, res) => {
  try {
    const { email, password, apiKey } = req.body;
    let authHeader;

    if (apiKey) {
      authHeader = `Bearer ${apiKey.trim()}`;
    } else if (email && password) {
      const { data } = await axios.post(`${PRIVATE_URL}/log_in`,
        { email: email.trim().toLowerCase(), password },
        { timeout: 10000, headers: { 'Content-Type': 'application/json' } }
      );
      if (!data.token) return res.json({ ok: false, error: 'Login returned no token — check credentials.' });
      authHeader = data.token;
    } else {
      return res.json({ ok: false, error: 'Provide email+password or an API key.' });
    }

    const bal = await axios.get(`${PRIVATE_URL}/portfolio/balance`, {
      headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
      timeout: 8000,
    });
    const balance = bal.data?.balance;
    res.json({ ok: true, balance: balance != null ? +(balance / 100).toFixed(2) : null });
  } catch (e) {
    const msg = e.response?.data?.message || e.response?.data?.error || e.message;
    res.json({ ok: false, error: msg });
  }
});

// Save Tradovate credentials to futures_settings DB
router.post('/futures-tradovate', requireAuth, (req, res) => {
  const { tv_username, tv_password, tv_cid, tv_sec, tv_device_id, tv_demo } = req.body;
  if (tv_username !== undefined) setFuturesSetting('tv_username', tv_username.trim());
  if (tv_password)               setFuturesSetting('tv_password', tv_password);
  if (tv_cid      !== undefined) setFuturesSetting('tv_cid',      tv_cid.trim());
  if (tv_sec)                    setFuturesSetting('tv_sec',      tv_sec);
  if (tv_device_id !== undefined) setFuturesSetting('tv_device_id', tv_device_id.trim() || 'sharp-bot-futures-001');
  setFuturesSetting('tv_demo', tv_demo === 'true' ? 'true' : 'false');
  res.redirect('/settings?saved=1');
});

router.post('/futures-trading-mode', requireAuth, (req, res) => {
  const mode = req.body.mode === 'live' ? 'live' : 'sim';
  setFuturesSetting('trading_mode', mode);
  res.redirect('/settings?saved=1');
});

// Clear all Kalshi credentials
router.post('/clear', requireAuth, (req, res) => {
  db.prepare('UPDATE users SET kalshi_email = NULL, kalshi_password_enc = NULL, kalshi_api_key_enc = NULL WHERE id = ?')
    .run(req.session.user.id);
  res.redirect('/settings');
});

module.exports = router;
