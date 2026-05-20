// utils/oddsApi.js
// Fetches live odds from The Odds API (https://the-odds-api.com)
// Aggressive caching to preserve free-tier quota (500 req/month)

const axios = require('axios');
const cache = require('./cache');

const BASE_URL = 'https://api.the-odds-api.com/v4';
const API_KEY = process.env.ODDS_API_KEY;

// ── Verified sport keys from The Odds API ────────────────────────────────────
const SPORT_MAP = {
  basketball_nba:         { name: 'NBA',             emoji: '🏀' },
  americanfootball_nfl:   { name: 'NFL',             emoji: '🏈' },
  baseball_mlb:           { name: 'MLB',             emoji: '⚾' },
  basketball_ncaab:       { name: "NCAA Men's Hoops",emoji: '🏀' },
  americanfootball_ncaaf: { name: 'NCAA Football',   emoji: '🏈' },
};

const MARKETS = ['h2h', 'spreads', 'totals'];

// Free plan: 500 req/month ≈ 16/day. Cache aggressively!
const CACHE_TTL = 30 * 60; // 30 minutes

async function fetchOddsForSport(sportKey) {
  const cacheKey = `odds_${sportKey}`;
  const cached = cache.get(cacheKey);
  if (cached) {
    console.log(`[cache HIT] ${sportKey}`);
    return cached;
  }

  console.log(`[API call] fetching ${sportKey}...`);

  try {
    const { data, headers } = await axios.get(`${BASE_URL}/sports/${sportKey}/odds`, {
      params: {
        apiKey: API_KEY,
        regions: 'us',
        markets: MARKETS.join(','),
        oddsFormat: 'american',
        dateFormat: 'iso',
      },
      timeout: 10000,
    });

    const remaining = headers['x-requests-remaining'] ?? '?';
    const used = headers['x-requests-used'] ?? '?';
    console.log(`[API] ${sportKey}: ${data.length} games | quota: ${used} used, ${remaining} remaining`);

    if (parseInt(remaining) < 50) {
      console.warn(`⚠️  WARNING: Only ${remaining} API requests remaining this month!`);
    }

    cache.set(cacheKey, data, CACHE_TTL);
    return data;
  } catch (err) {
    const status = err.response?.status;
    const msg = err.response?.data?.message ?? err.message;

    if (status === 401 && msg.includes('quota')) {
      console.error(`❌ QUOTA EXCEEDED for ${sportKey}. Renew at https://the-odds-api.com`);
      cache.set(cacheKey, [], CACHE_TTL);
      return [];
    }
    if (status === 404 || status === 422) {
      console.log(`ℹ️  ${sportKey} not active/available this season`);
      cache.set(cacheKey, [], CACHE_TTL);
      return [];
    }

    console.error(`Odds API error for ${sportKey}: HTTP ${status} — ${msg}`);
    return [];
  }
}

async function fetchAllOdds() {
  const results = await Promise.allSettled(
    Object.entries(SPORT_MAP).map(async ([key, meta]) => {
      const games = await fetchOddsForSport(key);
      return games.map(g => ({ ...g, sportKey: key, sportMeta: meta }));
    })
  );

  const allGames = [];
  for (const r of results) {
    if (r.status === 'fulfilled') allGames.push(...r.value);
  }

  console.log(`[fetchAllOdds] Total games: ${allGames.length}`);
  return allGames;
}

async function checkQuota() {
  try {
    const { headers } = await axios.get(`${BASE_URL}/sports`, {
      params: { apiKey: API_KEY },
      timeout: 8000,
    });
    return {
      used: headers['x-requests-used'] ?? 'unknown',
      remaining: headers['x-requests-remaining'] ?? 'unknown',
    };
  } catch {
    return { used: 'unknown', remaining: 'unknown' };
  }
}

module.exports = { fetchOddsForSport, fetchAllOdds, checkQuota, SPORT_MAP };
