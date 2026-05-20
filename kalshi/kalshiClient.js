// kalshi/kalshiClient.js
const axios = require('axios');
const crypto = require('crypto');
const fs = require('fs');
const cache = require('../utils/cache');

// PUBLIC market data — no auth needed (api.elections.kalshi.com serves ALL markets despite the name)
const PUBLIC_URL = 'https://api.elections.kalshi.com/trade-api/v2';

// PRIVATE trading endpoints — auth required
const PRIVATE_URL = 'https://trading-api.kalshi.com/trade-api/v2';

console.log('[Kalshi] Public URL (no auth):', PUBLIC_URL);

// ── Session token ──────────────────────────────────────────────────────────────
let sessionToken = null;
let tokenExpiry  = null;

// ── RSA signing ───────────────────────────────────────────────────────────────
function signRequest(method, path, timestampMs) {
  const keyPath = process.env.KALSHI_PRIVATE_KEY_PATH;
  if (!keyPath || !fs.existsSync(keyPath)) return null;
  try {
    const privateKey = fs.readFileSync(keyPath, 'utf8');
    const sign = crypto.createSign('SHA256');
    sign.update(`${timestampMs}${method.toUpperCase()}${path}`);
    sign.end();
    return sign.sign({ key: privateKey, padding: crypto.constants.RSA_PKCS1_PSS_PADDING }, 'base64');
  } catch (e) {
    console.error('[Kalshi] RSA sign error:', e.message);
    return null;
  }
}

// ── Password login ─────────────────────────────────────────────────────────────
async function loginWithPassword() {
  if (sessionToken && tokenExpiry && Date.now() < tokenExpiry) return sessionToken;

  const email    = process.env.KALSHI_EMAIL;
  const password = process.env.KALSHI_PASSWORD;
  if (!email || !password) throw new Error('Set KALSHI_EMAIL + KALSHI_PASSWORD in .env for portfolio/trading features');

  console.log('[Kalshi] Logging in to trading API...');
  const { data } = await axios.post(`${PRIVATE_URL}/log_in`, { email, password }, {
    timeout: 10000,
    headers: { 'Content-Type': 'application/json' },
  });

  sessionToken = data.token;
  if (!sessionToken) throw new Error('Login failed — no token returned. Check credentials.');
  tokenExpiry = Date.now() + 23 * 60 * 60 * 1000;
  console.log('[Kalshi] Login successful');
  return sessionToken;
}

// ── Auth headers (for private endpoints only) ─────────────────────────────────
async function authHeaders(method, path) {
  const apiKey = process.env.KALSHI_API_KEY;
  const ts     = Date.now().toString();
  if (apiKey) {
    const sig = signRequest(method, path, ts);
    if (sig) return { 'KALSHI-ACCESS-KEY': apiKey, 'KALSHI-ACCESS-TIMESTAMP': ts, 'KALSHI-ACCESS-SIGNATURE': sig };
    return { 'Authorization': `Bearer ${apiKey}` };
  }
  const token = await loginWithPassword();
  return { 'Authorization': token };
}

// ── Public request (no auth, uses api.elections.kalshi.com) ───────────────────
async function publicRequest(method, path, params = {}, cacheTTL = 0) {
  const cacheKey = `kalshi_pub_${path}_${JSON.stringify(params)}`;
  if (cacheTTL > 0) {
    const cached = cache.get(cacheKey);
    if (cached) return cached;
  }

  const { data } = await axios({
    method,
    url: `${PUBLIC_URL}${path}`,
    headers: { 'Content-Type': 'application/json' },
    params: method === 'GET' ? params : undefined,
    timeout: 12000,
  });

  if (cacheTTL > 0) cache.set(cacheKey, data, cacheTTL);
  return data;
}

// ── Private request (auth required, uses trading-api.kalshi.com) ──────────────
async function privateRequest(method, path, params = {}, body = null) {
  const headers = { 'Content-Type': 'application/json' };
  const auth = await authHeaders(method, `/trade-api/v2${path}`);
  Object.assign(headers, auth);

  const { data } = await axios({
    method,
    url: `${PRIVATE_URL}${path}`,
    headers,
    params: method === 'GET' ? params : undefined,
    data: body ? JSON.stringify(body) : undefined,
    timeout: 12000,
  });

  return data;
}

// ── PUBLIC endpoints ───────────────────────────────────────────────────────────

async function getMarkets({ status = 'open', limit = 100, cursor = null, seriesTicker = null, category = null } = {}) {
  const params = { status, limit };
  if (cursor)       params.cursor = cursor;
  if (seriesTicker) params.series_ticker = seriesTicker;
  if (category)     params.category = category;
  return publicRequest('GET', '/markets', params, 5 * 60);
}

async function getMarket(ticker) {
  return publicRequest('GET', `/markets/${ticker}`, {}, 2 * 60);
}

async function getOrderbook(ticker, depth = 10) {
  return publicRequest('GET', `/markets/${ticker}/orderbook`, { depth }, 30);
}

async function getMarketHistory(ticker, periodInterval = 60) {
  return publicRequest('GET', `/markets/${ticker}/history`, { period_interval: periodInterval }, 5 * 60);
}

async function getSeries(seriesTicker) {
  return publicRequest('GET', `/series/${seriesTicker}`, {}, 10 * 60);
}

async function getEvents({ status = 'open', limit = 100, category = null } = {}) {
  const params = { status, limit };
  if (category) params.category = category;
  return publicRequest('GET', '/events', params, 5 * 60);
}

async function searchMarkets(query, limit = 20) {
  const cacheKey = `kalshi_search_${query.toLowerCase()}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const data = await getMarkets({ status: 'open', limit: 200 });
  const markets = data?.markets || [];
  const q = query.toLowerCase();
  const results = markets.filter(m =>
    m.title?.toLowerCase().includes(q) ||
    m.ticker?.toLowerCase().includes(q) ||
    m.subtitle?.toLowerCase().includes(q)
  ).slice(0, limit);

  cache.set(cacheKey, results, 3 * 60);
  return results;
}

// Individual player-prop series tickers — these have real order books and prices.
// MVE bundle markets (KXMVECROSSCATEGORY, KXMVESPORTSMULTIGAMEEXTENDED) appear
// in the general /markets list but always have yes_bid_dollars=0 (no book).
const INDIVIDUAL_SERIES = [
  'KXNBAPTS', 'KXNBAAST', 'KXNBAREB', 'KXNBATHRPM', 'KXNBASTL', 'KXNBABLK',
  'KXNBATOTAL', 'KXNBAGAME',
  'KXMLBHITS', 'KXMLBHR', 'KXMLBRBI', 'KXMLBSO', 'KXMLBGAME', 'KXMLBTOTAL',
  'KXNFLPASS', 'KXNFLRUSH', 'KXNFLREC', 'KXNFLTD', 'KXNFLGAME', 'KXNFLTOTAL',
  'KXNHLGAME', 'KXNHLTOTAL',
];

// Game-only series — used by the upset detector (separate short-TTL cache)
const GAME_SERIES = ['KXNBAGAME', 'KXNFLGAME', 'KXMLBGAME', 'KXNHLGAME'];

let _gameCache    = null;
let _gameCacheAt  = 0;
const GAME_CACHE_TTL = 2 * 60 * 1000; // 2 minutes — prices move fast during live games
let _gameFetchPromise = null;

async function getGameMarkets() {
  if (_gameCache && Date.now() - _gameCacheAt < GAME_CACHE_TTL) return _gameCache;
  if (_gameFetchPromise) return _gameFetchPromise;
  _gameFetchPromise = _fetchGameMarkets().finally(() => { _gameFetchPromise = null; });
  return _gameFetchPromise;
}

async function _fetchGameMarkets() {
  const results = [];
  for (const series of GAME_SERIES) {
    // cacheTTL=0 → bypass publicRequest cache so stale empty results don't persist
    const markets = await publicRequest('GET', '/markets', { status: 'open', limit: 100, series_ticker: series }, 0)
      .then(d => d?.markets || [])
      .catch(err => { console.warn(`[getGameMarkets] ${series} failed:`, err.message); return []; });
    console.log(`[getGameMarkets] ${series}: ${markets.length} markets`);
    results.push(...markets);
    await new Promise(r => setTimeout(r, 200));
  }
  console.log(`[getGameMarkets] Total: ${results.length} game markets`);
  _gameCache   = results;
  _gameCacheAt = Date.now();
  return results;
}

// Module-level cache — shared across all callers (parlay + upsets) to avoid stampedes
let _sportsCache = null;
let _sportsCacheAt = 0;
const SPORTS_CACHE_TTL = 10 * 60 * 1000; // 10 minutes
let _sportsFetchPromise = null; // in-flight deduplication

async function getSportsMarkets(category = null) {
  // Return cached result if still fresh
  if (_sportsCache && Date.now() - _sportsCacheAt < SPORTS_CACHE_TTL) {
    return _sportsCache;
  }
  // If a fetch is already in flight, wait for it instead of making a second request
  if (_sportsFetchPromise) return _sportsFetchPromise;

  _sportsFetchPromise = _fetchSportsMarkets(category).finally(() => { _sportsFetchPromise = null; });
  return _sportsFetchPromise;
}

async function _fetchSportsMarkets(category = null) {
  // Fetch 2 pages of general markets + all individual player-prop series in parallel
  const generalPagesPromise = (async () => {
    const out = [];
    let cursor = null;
    for (let page = 0; page < 2; page++) {
      try {
        const params = { status: 'open', limit: 100 };
        if (cursor)   params.cursor   = cursor;
        if (category) params.category = category;
        const data = await publicRequest('GET', '/markets', params, 5 * 60);
        const markets = data?.markets || [];
        out.push(...markets);
        cursor = data?.cursor;
        if (!cursor || markets.length < 100) break;
      } catch (err) {
        console.warn(`[getSportsMarkets] General page ${page + 1} failed:`, err.message);
        if (out.length === 0 && page === 0) throw new Error('Failed to fetch any Kalshi markets');
        break;
      }
    }
    return out;
  })();

  // Fetch series one at a time — parallel bursts trigger 429s even in small batches
  const seriesResults = [];
  for (const series of INDIVIDUAL_SERIES) {
    const markets = await publicRequest('GET', '/markets', { status: 'open', limit: 100, series_ticker: series }, 10 * 60)
      .then(d => d?.markets || [])
      .catch(() => []);
    seriesResults.push(markets);
    await new Promise(r => setTimeout(r, 150)); // 150ms between each keeps well under rate limit
  }

  const generalMarkets = await generalPagesPromise;
  const seriesBatches  = seriesResults;
  const allMarkets = [...generalMarkets, ...seriesBatches.flat()];

  console.log(`[getSportsMarkets] Fetched ${generalMarkets.length} general + ${allMarkets.length - generalMarkets.length} series markets`);

  // Sports detection: keep anything from a known sport prefix or matching keywords
  const TICKER_PREFIXES = [
    'KXMVESPO', 'KXMVECRO',
    'KXNBA', 'KXNFL', 'KXMLB', 'KXNHL', 'KXNCAA', 'KXUFC', 'KXWTA', 'KXATP',
  ];
  const SPORT_KEYWORDS = [
    'lebron','curry','durant','giannis','luka','jokic','embiid','tatum','brown',
    'edwards','maxey','sengun','oubre','george','hartenstein','dort','avdija','white','pritchard',
    'points','rebounds','assists','steals','blocks','threes',
    'strikeouts','home run','rushing yards','passing yards','touchdown',
    'celtics','knicks','warriors','lakers','nuggets','heat','thunder',
    'pacers','cavaliers','bucks','rockets','grizzlies','clippers',
    'chiefs','eagles','bills','cowboys','packers','ravens',
    'yankees','dodgers','mets','cubs','astros','braves',
    'nba','nfl','mlb','nhl','ncaa','ufc',
    'playoff','finals','championship',
    'fils','paul','sinner','alcaraz','djokovic','medvedev','swiatek','sabalenka','gauff',
  ];

  const filtered = allMarkets.filter(m => {
    const ticker = (m.ticker || '').toUpperCase();
    const text   = `${m.title || ''} ${m.ticker || ''} ${m.no_sub_title || ''}`.toLowerCase();
    return TICKER_PREFIXES.some(p => ticker.startsWith(p)) ||
           SPORT_KEYWORDS.some(k => text.includes(k));
  });

  // Deduplicate by ticker
  const seen = new Set();
  const deduped = filtered.filter(m => {
    if (seen.has(m.ticker)) return false;
    seen.add(m.ticker);
    return true;
  });

  console.log(`[getSportsMarkets] ${deduped.length} unique sports markets`);
  _sportsCache   = deduped;
  _sportsCacheAt = Date.now();
  return deduped;
}

// ── PRIVATE endpoints ──────────────────────────────────────────────────────────

async function getBalance() {
  return privateRequest('GET', '/portfolio/balance');
}

async function getPositions({ limit = 100 } = {}) {
  return privateRequest('GET', '/portfolio/positions', { limit });
}

async function getOrders({ status = 'resting', limit = 100 } = {}) {
  return privateRequest('GET', '/portfolio/orders', { status, limit });
}

async function getTrades({ limit = 50 } = {}) {
  return privateRequest('GET', '/portfolio/trades', { limit });
}

async function getFills({ limit = 50 } = {}) {
  return privateRequest('GET', '/portfolio/fills', { limit });
}

async function placeOrder(ticker, side, type, count, price = null) {
  const body = {
    ticker, action: 'buy', side, type, count,
    ...(type === 'limit' && price != null ? { yes_price: side === 'yes' ? price : 100 - price } : {}),
  };
  return privateRequest('POST', '/portfolio/orders', {}, body);
}

async function cancelOrder(orderId) {
  return privateRequest('DELETE', `/portfolio/orders/${orderId}`);
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function priceToDisplay(yesPrice) {
  if (yesPrice == null) return { yes: 'N/A', no: 'N/A', prob: null, pct: 'N/A' };
  return {
    yes:  `${yesPrice}¢`,
    no:   `${100 - yesPrice}¢`,
    prob: yesPrice / 100,
    pct:  `${yesPrice}%`,
  };
}

function calcPayout(contracts, yesPrice, side) {
  const costPerContract = (side === 'yes' ? yesPrice : 100 - yesPrice) / 100;
  const totalCost   = contracts * costPerContract;
  const profit      = contracts - totalCost;
  const roi         = ((profit / totalCost) * 100).toFixed(1);
  return { totalCost: totalCost.toFixed(2), profit: profit.toFixed(2), roi, payout: contracts.toFixed(2) };
}

module.exports = {
  publicRequest,
  getMarkets, getMarket, getOrderbook, getMarketHistory, getSeries, getEvents,
  searchMarkets, getSportsMarkets, getGameMarkets,
  getBalance, getPositions, getOrders, getTrades, getFills,
  placeOrder, cancelOrder,
  priceToDisplay, calcPayout,
};
