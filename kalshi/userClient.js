// kalshi/userClient.js — per-user Kalshi client factory
// Supports two auth methods: email+password (session token) or API key (Bearer).
// Google OAuth users should use the API key option.
const axios  = require('axios');
const { decrypt } = require('../utils/encrypt');

const PRIVATE_URL = 'https://trading-api.kalshi.com/trade-api/v2';

// In-memory session cache for email+password auth: userId → { token, expiry }
const _sessions = {};

async function getSessionToken(userId, email, password) {
  const s = _sessions[userId];
  if (s && s.token && Date.now() < s.expiry) return s.token;

  const { data } = await axios.post(`${PRIVATE_URL}/log_in`, { email, password }, {
    timeout: 10000,
    headers: { 'Content-Type': 'application/json' },
  });
  if (!data.token) throw new Error('Kalshi login failed — check your credentials in Settings.');
  _sessions[userId] = { token: data.token, expiry: Date.now() + 22 * 3600 * 1000 };
  return data.token;
}

function invalidateSession(userId) {
  delete _sessions[userId];
}

async function privateRequest(userId, credentials, method, path, params = {}, body = null) {
  let authHeader;
  if (credentials.apiKey) {
    // API key auth — works for Google OAuth users and anyone who prefers it
    authHeader = `Bearer ${credentials.apiKey}`;
  } else {
    authHeader = await getSessionToken(userId, credentials.email, credentials.password);
  }

  const { data } = await axios({
    method,
    url: `${PRIVATE_URL}${path}`,
    headers: { 'Content-Type': 'application/json', Authorization: authHeader },
    params: method === 'GET' ? params : undefined,
    data:   body ? JSON.stringify(body) : undefined,
    timeout: 12000,
  });
  return data;
}

// Build a client for one user given their DB row
function buildClient(userRow) {
  const { id, kalshi_email, kalshi_password_enc, kalshi_api_key_enc } = userRow;

  const apiKey   = kalshi_api_key_enc  ? decrypt(kalshi_api_key_enc)  : null;
  const password = kalshi_password_enc ? decrypt(kalshi_password_enc) : null;

  const hasCredentials = !!(apiKey || (kalshi_email && password));

  if (!hasCredentials) {
    const err = () => Promise.reject(
      new Error('Kalshi credentials not set. Go to Settings to add your API key or email+password.')
    );
    return { getBalance: err, getPositions: err, getOrders: err, placeOrder: err, cancelOrder: err, hasCredentials: false, invalidateSession: () => {} };
  }

  const credentials = apiKey
    ? { apiKey }
    : { email: kalshi_email, password };

  const req = (method, path, params, body) => privateRequest(id, credentials, method, path, params, body);

  return {
    hasCredentials: true,
    authMethod: apiKey ? 'apikey' : 'password',
    getBalance:   ()                                     => req('GET',    '/portfolio/balance'),
    getPositions: ({ limit = 100 } = {})                 => req('GET',    '/portfolio/positions', { limit }),
    getOrders:    ({ status = 'resting', limit = 100 } = {}) =>
                                                            req('GET',    '/portfolio/orders', { status, limit }),
    placeOrder: (ticker, side, type, count, price) => {
      const body = {
        ticker, action: 'buy', side, type, count,
        ...(type === 'limit' && price != null ? { yes_price: side === 'yes' ? price : 100 - price } : {}),
      };
      return req('POST', '/portfolio/orders', {}, body);
    },
    cancelOrder:       (orderId) => req('DELETE', `/portfolio/orders/${orderId}`),
    invalidateSession: ()        => invalidateSession(id),
  };
}

// Fetch the user row from DB and return a ready-to-use client
function getClient(userId, db) {
  const row = db.prepare('SELECT id, kalshi_email, kalshi_password_enc, kalshi_api_key_enc FROM users WHERE id = ?').get(userId);
  if (!row) throw new Error('User not found');
  return buildClient(row);
}

module.exports = { getClient, buildClient };
