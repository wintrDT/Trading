// web/routes/spreads.js
const router = require('express').Router();
const axios  = require('axios');
const { requireAuth } = require('../middleware');
const { publicRequest, getMarket, getSportsMarkets } = require('../../kalshi/kalshiClient');
const { getClient } = require('../../kalshi/userClient');
const db    = require('../db');
const cache = require('../../utils/cache');
const { logTrade }            = require('../../utils/tradeLogger');
const { saveExecutedParlay }  = require('../../utils/pickTracker');
const { fetchSportsContext, avgFieldFromTicker, isMLBTicker } = require('../../utils/sportsData');
const { normalizeName } = require('../../utils/propsApi');

function normalizeMarket(m) {
  const cents = v => v != null && !isNaN(v) ? Math.round(parseFloat(v) <= 1 ? parseFloat(v) * 100 : parseFloat(v)) : null;
  const subtitle  = m.yes_sub_title || m.no_sub_title || m.subtitle || null;
  const baseTitle = m.title || m.ticker;
  const title     = subtitle && subtitle !== baseTitle ? subtitle : (m.no_sub_title || baseTitle);
  return {
    ticker:       m.ticker,
    title,
    fullTitle:    baseTitle,
    category:     getMarketCategory(m.ticker),
    seriesTicker: m.series_ticker || null,
    openTime:     m.open_time  || null,
    closeTime:    m.close_time || null,
    yesBid:  cents(m.yes_bid_dollars),
    yesAsk:  cents(m.yes_ask_dollars),
    noBid:   cents(m.no_bid_dollars),
    noAsk:   cents(m.no_ask_dollars),
    last:    cents(m.last_price_dollars),
  };
}

// ── Crypto signal helpers ──────────────────────────────────────────────────────

const CRYPTO_META = {
  KXBTC:  { geckoId: 'bitcoin',   symbol: 'BTC' },
  KXETH:  { geckoId: 'ethereum',  symbol: 'ETH' },
  KXDOGE: { geckoId: 'dogecoin',  symbol: 'DOGE' },
};

const CRYPTO_KEYWORDS = {
  btc: 'KXBTC', bitcoin: 'KXBTC',
  eth: 'KXETH', ethereum: 'KXETH',
  doge: 'KXDOGE', dogecoin: 'KXDOGE',
};

// Keywords that trigger the sports market pool (uses getSportsMarkets cache)
const SPORTS_KEYWORDS = new Set([
  'nba','basketball','nfl','football','mlb','baseball','nhl','hockey','ncaa','ufc','mma','sports',
  'celtics','knicks','warriors','lakers','nuggets','heat','thunder','pacers','cavaliers','cavs',
  'bucks','rockets','grizzlies','clippers','spurs','nets','sixers','bulls','hawks','hornets',
  'magic','wizards','raptors','pistons','pelicans','suns','kings','wolves','timberwolves','jazz',
  'blazers','chiefs','eagles','bills','cowboys','packers','ravens','bengals','lions','bears',
  'giants','jets','patriots','dolphins','steelers','chargers','broncos','raiders','seahawks',
  'rams','saints','falcons','panthers','buccaneers','texans','colts','titans','jaguars','vikings',
  'commanders','browns','yankees','dodgers','mets','cubs','astros','braves','padres','phillies',
  'mariners','guardians','rays','tigers','reds','rangers','bruins','leafs','lightning','oilers',
  'hurricanes','avalanche','lebron','curry','durant','giannis','jokic','embiid','tatum','sga',
  'points','rebounds','assists','strikeouts','home run','rushing','passing','touchdown',
]);

// Derive a display category from a market ticker
function getMarketCategory(ticker) {
  const t = ticker.toUpperCase();
  if (/GAME-/.test(t))                              return 'game';
  if (/TOTAL/.test(t))                              return 'total';
  if (/PTS|AST|REB|THRPM|STL|BLK|HITS|HR\b|RBI|SO\b|KXMLBBB|KXMLBERA/.test(t)) return 'props';
  if (/KXNBA-|KXNFL-|KXMLB-|KXNHL-|KXNCAA-|KXUFC-/.test(t)) return 'championship';
  if (/KXBTC|KXETH|KXDOGE/.test(t))                return 'crypto';
  return 'other';
}

// Annotate normalized player-prop markets with injury/recent-form data.
// Markets for Out/Doubtful players are removed.
async function applyRealWorldAnnotations(markets) {
  try {
    const ctx = await fetchSportsContext(markets, null);
    const { injuries, mlbInjuries, playerAvgs, recentAvgs, b2bTeams } = ctx;

    return markets.map(m => {
      const avgField = avgFieldFromTicker(m.ticker || '');
      if (!avgField) return m;

      const raw   = m.title || '';
      const match = raw.match(/^([^:,0-9][^:]{2,}):\s*([\d.]+)\+/);
      if (!match) return m;

      const playerName = match[1].trim();
      const threshold  = parseFloat(match[2]);
      const norm       = normalizeName(playerName);
      const mlb        = isMLBTicker(m.ticker);

      // Injury gate (use correct sport's injury list)
      const injuryMap    = mlb ? mlbInjuries : injuries;
      const injuryStatus = injuryMap?.get(norm);
      if (injuryStatus === 'out' || injuryStatus === 'doubtful') return null;

      const seasonAvg = playerAvgs?.get(norm);
      const recent    = recentAvgs?.get(norm);

      // NBA-only: minutes gate (MLB players don't have a minutes concept)
      if (!mlb && seasonAvg?.minutes != null && seasonAvg.minutes < 15) return null;

      let confidence = 0;
      let note       = null;

      // NBA-only: B2B penalty
      if (!mlb && b2bTeams?.size && seasonAvg?.team && b2bTeams.has(seasonAvg.team)) {
        confidence -= 15;
        note = 'B2B rest risk';
      }

      // Injury status
      if (injuryStatus === 'questionable') { confidence -= 20; note = 'questionable'; }
      else if (injuryStatus === 'dtd')     { confidence -= 10; note = 'day-to-day'; }

      // Prefer recent form (L5) over season avg
      const src      = recent ?? seasonAvg;
      const statAvg  = src?.[avgField];
      const isRecent = recent?.[avgField] != null;
      const label    = isRecent ? `L5 avg ${statAvg}` : `avg ${statAvg}`;

      if (statAvg != null && threshold > 0) {
        const ratio = statAvg / threshold;
        if      (ratio >= 1.5)  { confidence += 15; note = `${label} >> ${threshold} (hot)`; }
        else if (ratio >= 1.25) { confidence += 10; note = `${label} > ${threshold}`; }
        else if (ratio >= 1.05) { confidence +=  5; note = label; }
        else if (ratio < 0.55)  return null;
        else if (ratio < 0.85)  { confidence -= 15; note = `${label} < ${threshold} (cold)`; }
      }

      // Contextual note fallback
      if (!note) {
        if (!mlb && seasonAvg?.minutes != null) note = `${seasonAvg.minutes} min/g`;
        else if (mlb && seasonAvg?.avg   != null) note = `.${String(Math.round(seasonAvg.avg * 1000)).padStart(3,'0')} avg`;
      }

      // Full player stat line for UI display
      let playerStatLine = null;
      if (src) {
        const statsLabel = isRecent ? 'L5' : 'Avg';
        const parts = [];
        if (mlb) {
          if (src.hits != null && src.hits > 0)  parts.push(`${src.hits}H`);
          if (src.hr   != null && src.hr   > 0)  parts.push(`${src.hr}HR`);
          if (src.rbi  != null && src.rbi  > 0)  parts.push(`${src.rbi}RBI`);
          if (src.so   != null && src.so   > 0)  parts.push(`${src.so}K`);
          if (src.bb   != null && src.bb   > 0)  parts.push(`${src.bb}BB`);
        } else {
          if (src.pts  != null && src.pts  > 0)   parts.push(`${src.pts}pts`);
          if (src.reb  != null && src.reb  > 0)   parts.push(`${src.reb}reb`);
          if (src.ast  != null && src.ast  > 0)   parts.push(`${src.ast}ast`);
          if (src.fg3m != null && src.fg3m > 0)   parts.push(`${src.fg3m}3PM`);
          if (src.stl  != null && src.stl  > 0.3) parts.push(`${src.stl}stl`);
          if (src.blk  != null && src.blk  > 0.3) parts.push(`${src.blk}blk`);
        }
        if (parts.length > 1) playerStatLine = `${statsLabel}: ${parts.join(' · ')}`;
      }

      return { ...m, _confidence: confidence, _realWorldNote: note, _playerStatLine: playerStatLine };
    }).filter(Boolean);
  } catch (err) {
    console.warn('[spreads] real-world annotation failed:', err.message);
    return markets;
  }
}

async function fetchCryptoPrice(geckoId) {
  const { data } = await axios.get(
    `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=${geckoId}&price_change_percentage=1h,24h`,
    { timeout: 6000 }
  );
  const coin = data[0];
  return {
    price:     coin.current_price,
    change1h:  +(coin.price_change_percentage_1h_in_currency ?? 0).toFixed(2),
    change24h: +(coin.price_change_percentage_24h             ?? 0).toFixed(2),
  };
}

// Fetch 2 days of hourly prices and compute realized hourly volatility (σ as %)
async function fetchVolatility(geckoId) {
  const { data } = await axios.get(
    `https://api.coingecko.com/api/v3/coins/${geckoId}/market_chart?vs_currency=usd&days=2`,
    { timeout: 8000 }
  );
  const prices = (data.prices || []).map(p => p[1]);
  if (prices.length < 4) return 1.0; // fallback 1% if no data

  // Log returns between consecutive hourly points
  const returns = [];
  for (let i = 1; i < prices.length; i++) {
    returns.push(Math.log(prices[i] / prices[i - 1]));
  }
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((a, r) => a + (r - mean) ** 2, 0) / returns.length;
  return +(Math.sqrt(variance) * 100).toFixed(4); // hourly σ in %
}

// Abramowitz & Stegun normal CDF approximation (max error 7.5e-8)
function normalCDF(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422820 * Math.exp(-x * x / 2);
  const p = t * (0.3193815 + t * (-0.3565638 + t * (1.7814779 + t * (-1.8212560 + t * 1.3302744))));
  const prob = 1 - d * p;
  return x >= 0 ? prob : 1 - prob;
}

// Log-normal win probability for each market type
function calcWinProb(mtype, currentPrice, threshold, lower, upper, hourlyVol, minutesLeft) {
  const sigma = (hourlyVol || 1) / 100;
  const t     = Math.max(minutesLeft, 1) / 60;
  const s     = sigma * Math.sqrt(t);
  if (s === 0) return null;

  if (mtype === 'above' && threshold) {
    // P(S_T >= threshold) = Φ( log(S0/threshold) / s )
    return Math.min(99, Math.max(1, Math.round(normalCDF(Math.log(currentPrice / threshold) / s) * 100)));
  }
  if (mtype === 'below' && threshold) {
    // P(S_T <= threshold) = 1 - P(S_T > threshold) = Φ( log(threshold/S0) / s )
    return Math.min(99, Math.max(1, Math.round(normalCDF(Math.log(threshold / currentPrice) / s) * 100)));
  }
  if (mtype === 'range' && lower != null && upper != null) {
    // P(lower <= S_T <= upper) = Φ(log(S0/lower)/s) - Φ(log(S0/upper)/s)
    const pAboveLower = normalCDF(Math.log(currentPrice / lower) / s);
    const pAboveUpper = normalCDF(Math.log(currentPrice / upper) / s);
    return Math.min(99, Math.max(1, Math.round((pAboveLower - pAboveUpper) * 100)));
  }
  return null;
}

// Parse market type from subtitle title (already normalized by normalizeMarket):
//   "above" → "$86,300 or above"
//   "below" → "$X or below"
//   "range" → "$79,100 to 79,199.99"  (the common BTC bucket format)
function parseMarketType(title) {
  const num = '[\\d,]+(?:\\.\\d+)?';
  let m;
  m = title.match(new RegExp(`\\$(${num})\\s+or\\s+above`, 'i'));
  if (m) return { mtype: 'above', threshold: parseFloat(m[1].replace(/,/g, '')) };
  m = title.match(new RegExp(`\\$(${num})\\s+or\\s+below`, 'i'));
  if (m) return { mtype: 'below', threshold: parseFloat(m[1].replace(/,/g, '')) };
  // Range: "$X to $Y" or "$X to Y" or "$X - $Y"
  m = title.match(new RegExp(`\\$(${num})\\s+(?:to|-)\\s+\\$?(${num})`, 'i'));
  if (m) return { mtype: 'range', lower: parseFloat(m[1].replace(/,/g, '')), upper: parseFloat(m[2].replace(/,/g, '')) };
  return null;
}

function buildSignal(normalizedMarkets, currentPrice, change1h, change24h, symbol, hourlyVol) {
  const marketsToUse = normalizedMarkets;

  // Detect contract window + minutes remaining
  const sample = marketsToUse.find(m => m.openTime && m.closeTime);
  const windowMinutes = sample
    ? Math.round((new Date(sample.closeTime) - new Date(sample.openTime)) / 60000)
    : null;
  const minutesLeft = sample
    ? Math.max(1, Math.round((new Date(sample.closeTime) - Date.now()) / 60000))
    : 60;

  const enriched = marketsToUse.map(m => {
    const mt = parseMarketType(m.title || '');
    if (!mt) return { ...m, mtype: null, itm: null, pctAway: null, winProb: null, ev: null, kelly: null };

    let itm, distance;
    if (mt.mtype === 'above') {
      itm      = currentPrice >= mt.threshold;
      distance = currentPrice - mt.threshold;
    } else if (mt.mtype === 'below') {
      itm      = currentPrice <= mt.threshold;
      distance = mt.threshold - currentPrice;
    } else {
      itm      = currentPrice >= mt.lower && currentPrice <= mt.upper;
      distance = itm
        ? Math.min(currentPrice - mt.lower, mt.upper - currentPrice)
        : currentPrice < mt.lower ? mt.lower - currentPrice : currentPrice - mt.upper;
    }

    const pctAway = +(Math.abs(distance) / currentPrice * 100).toFixed(2);
    const winProb = calcWinProb(mt.mtype, currentPrice,
      mt.threshold ?? null, mt.lower ?? null, mt.upper ?? null,
      hourlyVol, minutesLeft);

    // EV (cents per contract) = winProb% − ask price in cents
    // Kelly fraction = EV / (100 − cost)  [how much of bankroll to risk]
    const cost  = m.yesAsk ?? m.last ?? null;
    const ev    = (winProb != null && cost != null)
      ? +(winProb - cost).toFixed(1)
      : null;
    const kelly = (ev != null && cost != null && cost > 0 && cost < 100)
      ? +Math.max(0, (winProb - cost) / (100 - cost) * 100).toFixed(1)
      : null;

    return { ...m, mtype: mt.mtype,
      lower: mt.lower ?? null, upper: mt.upper ?? null, threshold: mt.threshold ?? null,
      itm, pctAway, winProb, ev, kelly };
  });

  const trendingUp = change1h >= 0;

  // Sort by EV descending — highest edge first (nulls last)
  const allByEV = enriched
    .filter(m => m.ev != null)
    .sort((a, b) => (b.ev - a.ev) || ((b.winProb || 0) - (a.winProb || 0)));

  const pick = m => m ? {
    ticker: m.ticker, title: m.title, threshold: m.threshold,
    lower: m.lower, upper: m.upper,
    mtype: m.mtype, itm: m.itm, pctAway: m.pctAway,
    winProb: m.winProb, ev: m.ev, kelly: m.kelly,
    side: 'yes', closeTime: m.closeTime,
  } : null;

  // Only consider range markets within 3% of current price — eliminates extreme tails.
  // Sort: ITM first, then by EV descending, then by win probability.
  const nearMoney = enriched
    .filter(m => m.mtype === 'range' && m.pctAway != null && m.pctAway <= 3 && m.winProb >= 5)
    .sort((a, b) => {
      if (a.itm !== b.itm) return a.itm ? -1 : 1; // ITM first
      const aEV = a.ev ?? -Infinity;
      const bEV = b.ev ?? -Infinity;
      return (bEV - aEV) || ((b.winProb || 0) - (a.winProb || 0));
    });

  let topPicks = nearMoney.slice(0, 3);

  // Fallback: widen to 10% if nothing within 3%
  if (topPicks.length === 0) {
    topPicks = enriched
      .filter(m => m.mtype === 'range' && m.pctAway != null && m.pctAway <= 10 && m.winProb >= 3)
      .sort((a, b) => (b.winProb || 0) - (a.winProb || 0))
      .slice(0, 3);
  }

  const picks = topPicks.map(pick);

  // Display list sorted by EV descending (nulls last), then winProb
  const sorted = [...enriched]
    .filter(m => m.pctAway != null)
    .sort((a, b) => {
      const aEV = a.ev ?? -Infinity;
      const bEV = b.ev ?? -Infinity;
      return (bEV - aEV) || ((b.winProb || 0) - (a.winProb || 0));
    });

  return {
    price: currentPrice, change1h, change24h, symbol, trendingUp,
    hourlyVol: +hourlyVol.toFixed(2), windowMinutes, minutesLeft,
    closeTime: sample?.closeTime || null,
    picks,
    enriched: sorted,
  };
}

// ── Routes ─────────────────────────────────────────────────────────────────────

router.get('/', requireAuth, (req, res) => {
  let hasKalshiCreds = false;
  try { hasKalshiCreds = getClient(req.session.user.id, db).hasCredentials; } catch {}
  res.render('spreads', { user: req.session.user, hasKalshiCreds });
});

router.get('/search', requireAuth, async (req, res) => {
  try {
    const q    = (req.query.q || '').trim();
    const qLow = q.toLowerCase();
    const cacheKey = `spread_search_${qLow}`;
    const cached   = cache.get(cacheKey);
    if (cached) return res.json(cached);

    const cryptoSeries = CRYPTO_KEYWORDS[qLow];

    let results = [];
    let signal  = null;

    if (cryptoSeries) {
      const meta = CRYPTO_META[cryptoSeries];
      const [mktRes, priceRes, volRes] = await Promise.allSettled([
        publicRequest('GET', '/markets', { status: 'open', series_ticker: cryptoSeries, limit: 100 }, 0),
        fetchCryptoPrice(meta.geckoId),
        fetchVolatility(meta.geckoId),
      ]);

      const normalized = (mktRes.value?.markets || []).map(normalizeMarket);
      const hourlyVol  = volRes.status === 'fulfilled' ? volRes.value : 1.0;

      if (priceRes.status === 'fulfilled') {
        const { price, change1h, change24h } = priceRes.value;
        const { enriched, picks, trendingUp, windowMinutes, minutesLeft, closeTime, hourlyVol: vol }
          = buildSignal(normalized, price, change1h, change24h, meta.symbol, hourlyVol);
        results = enriched.slice(0, 30);
        signal  = { price, change1h, change24h, symbol: meta.symbol, trendingUp,
                    picks, windowMinutes, minutesLeft, closeTime, hourlyVol: vol };
      } else {
        results = normalized.sort((a, b) => (b.volume || 0) - (a.volume || 0)).slice(0, 30);
      }

      results = results.filter(m => m.yesAsk != null || m.yesBid != null || m.threshold != null);
    } else if (SPORTS_KEYWORDS.has(qLow) || (!q && false)) {
      // Sports search — pull from getSportsMarkets() cache (covers all series: games, props, champs)
      const allSports = await getSportsMarkets();
      let filtered = allSports;
      if (q.length >= 2) {
        filtered = allSports.filter(m => {
          const text = `${m.title || ''} ${m.ticker || ''} ${m.no_sub_title || ''} ${m.yes_sub_title || ''}`.toLowerCase();
          return text.includes(qLow);
        });
      }
      // Deduplicate by ticker (general + per-series fetches create duplicates),
      // then filter for priced markets. No hard slice — Reb/Ast/Blk series come
      // after Pts in the array and a low slice limit was silently dropping them.
      const seenTickers = new Set();
      const normalized = filtered
        .map(normalizeMarket)
        .filter(m => {
          if (!m.ticker || seenTickers.has(m.ticker)) return false;
          seenTickers.add(m.ticker);
          return m.yesAsk != null || m.yesBid != null || m.last != null;
        });
      results = await applyRealWorldAnnotations(normalized);
    } else {
      // Generic Kalshi API search for non-sports queries (fed rate, president, etc.)
      const params = { status: 'open', limit: 50 };
      if (q.length >= 2) params.search_query = q;
      const data = await publicRequest('GET', '/markets', params, 0);
      results = (data?.markets || [])
        .filter(m => !m.ticker.startsWith('KXMVE'))
        .map(normalizeMarket)
        .filter(m => m.yesAsk != null || m.yesBid != null || m.last != null);
    }

    const out = { results, signal };
    cache.set(cacheKey, out, 30);
    res.json(out);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Find sibling markets in the same series
router.get('/related', requireAuth, async (req, res) => {
  try {
    const { ticker } = req.query;
    if (!ticker) return res.json({ results: [] });

    const cacheKey = `spread_related_${ticker}`;
    const cached   = cache.get(cacheKey);
    if (cached) return res.json(cached);

    const marketData  = await getMarket(ticker);
    const market      = marketData?.market ?? marketData;
    const seriesTicker = market?.series_ticker;

    if (!seriesTicker) {
      cache.set(cacheKey, { results: [] }, 5 * 60);
      return res.json({ results: [] });
    }

    const data = await publicRequest('GET', '/markets', {
      status: 'open', series_ticker: seriesTicker, limit: 100,
    }, 5 * 60);

    const results = (data?.markets || [])
      .filter(m => m.ticker !== ticker)
      .map(normalizeMarket);

    const out = { results, seriesTicker };
    cache.set(cacheKey, out, 5 * 60);
    res.json(out);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Execute all legs using the requesting user's Kalshi account
router.post('/execute', requireAuth, async (req, res) => {
  try {
    const { legs } = req.body;
    if (!Array.isArray(legs) || legs.length === 0 || legs.length > 30) {
      return res.status(400).json({ error: 'Provide 1–30 legs' });
    }
    for (const leg of legs) {
      if (!leg.ticker || !['yes', 'no'].includes(leg.side)) {
        return res.status(400).json({ error: `Invalid leg: ${JSON.stringify(leg)}` });
      }
      const count = parseInt(leg.count);
      if (!Number.isInteger(count) || count < 1 || count > 999) {
        return res.status(400).json({ error: 'count must be 1–999 per leg' });
      }
      if (leg.type === 'limit') {
        const price = parseInt(leg.price);
        if (!Number.isInteger(price) || price < 1 || price > 99) {
          return res.status(400).json({ error: 'Limit price must be 1–99 cents' });
        }
      }
    }

    const client = getClient(req.session.user.id, db);
    if (!client.hasCredentials) {
      return res.status(403).json({
        ok: false,
        error: 'Kalshi credentials not set up. Go to Settings → Kalshi Account to add them.',
      });
    }

    const results = [];
    let hadFailure = false;

    for (const leg of legs) {
      try {
        const type     = leg.type === 'limit' ? 'limit' : 'market';
        const price    = type === 'limit' ? parseInt(leg.price) : null;
        const order    = await client.placeOrder(leg.ticker, leg.side, type, parseInt(leg.count), price);
        const orderId  = order?.order?.order_id ?? order?.order_id ?? null;
        const fillPrice = leg.side === 'yes'
          ? (order?.order?.yes_price ?? price ?? null)
          : (order?.order?.no_price  ?? price ?? null);
        results.push({
          ticker: leg.ticker, side: leg.side, count: parseInt(leg.count),
          type,   ...(price != null ? { limitPrice: price } : {}),
          fillPrice,
          status: 'filled',
          orderId,
        });
        try { logTrade({ ticker: leg.ticker, side: leg.side, type, count: parseInt(leg.count), fillPrice, orderId }); } catch {}
      } catch (err) {
        hadFailure = true;
        // If auth failed, invalidate the cached token so next attempt re-logs in
        if (err.response?.status === 401 || err.response?.status === 403) client.invalidateSession();
        results.push({
          ticker: leg.ticker, side: leg.side, count: leg.count,
          status: 'failed',
          error:  err.response?.data?.message ?? err.message ?? 'Unknown error',
        });
      }
    }

    const filled = results.filter(r => r.status === 'filled').length;
    const failed = results.filter(r => r.status === 'failed').length;

    // Save to pick tracker so the dashboard can show it with P&L
    if (filled >= 1) {
      try {
        const filledLegs = results.filter(r => r.status === 'filled');
        const wager      = filledLegs.reduce((sum, r) => sum + (r.count * (r.fillPrice ?? r.limitPrice ?? 50)) / 100, 0);
        const maxPayout  = filledLegs.reduce((sum, r) => sum + r.count, 0);
        const date       = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
        saveExecutedParlay(filledLegs, wager, maxPayout, date);
      } catch {}
    }

    res.status(hadFailure ? 207 : 200).json({
      ok: !hadFailure, filled, failed, results,
      message: !hadFailure
        ? `All ${filled} leg(s) filled`
        : `${filled} filled, ${failed} failed — check your portfolio for partial positions`,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
