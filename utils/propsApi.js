// utils/propsApi.js
// Fetches player prop odds from The Odds API and builds a lookup map
// used to find edge vs. Kalshi's implied probabilities.
// Cached aggressively (60 min) to preserve free-tier quota.

const axios = require('axios');
const cache = require('./cache');

const BASE_URL  = 'https://api.the-odds-api.com/v4';
const API_KEY   = process.env.ODDS_API_KEY;
const CACHE_TTL = 60 * 60; // 60 minutes

const PROP_SPORTS = ['basketball_nba', 'baseball_mlb'];

const PROP_MARKETS = [
  'player_points', 'player_rebounds', 'player_assists',
  'player_threes', 'player_steals',   'player_blocks',
  'batter_hits',   'batter_home_runs','batter_rbis',
  'pitcher_strikeouts',
].join(',');

// American odds → raw implied probability (includes vig)
function americanToProb(american) {
  if (american < 0) return (-american) / (-american + 100);
  return 100 / (american + 100);
}

// Build lookup: `playerNorm|statMarket|line` → { overProb, underProb, line, bookCount }
function buildPropMap(eventsList) {
  const raw = {};

  for (const event of eventsList) {
    for (const book of (event.bookmakers ?? [])) {
      for (const market of (book.markets ?? [])) {
        for (const outcome of (market.outcomes ?? [])) {
          const player = normalizeName(outcome.name ?? '');
          const side   = (outcome.description ?? '').toLowerCase(); // 'over' | 'under'
          const line   = outcome.point;
          const prob   = americanToProb(outcome.price);
          if (!player || !side || line == null) continue;

          const key = `${player}|${market.key}|${line}`;
          if (!raw[key]) raw[key] = { line, statKey: market.key, player, overProbs: [], underProbs: [] };
          if (side === 'over')  raw[key].overProbs.push(prob);
          if (side === 'under') raw[key].underProbs.push(prob);
        }
      }
    }
  }

  const result = {};
  for (const [key, val] of Object.entries(raw)) {
    if (!val.overProbs.length) continue;
    const avg = arr => arr.reduce((s, v) => s + v, 0) / arr.length;
    result[key] = {
      ...val,
      overProb:   avg(val.overProbs),
      underProb:  val.underProbs.length ? avg(val.underProbs) : 1 - avg(val.overProbs),
      bookCount:  val.overProbs.length,
    };
  }
  return result;
}

function normalizeName(name) {
  return name.toLowerCase()
    .replace(/\bjr\.?\b|\bsr\.?\b|\bii\b|\biii\b/gi, '')
    .replace(/[^a-z\s]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

async function fetchPlayerProps() {
  const cacheKey = 'vegas_prop_map';
  const cached   = cache.get(cacheKey);
  if (cached) return cached;

  if (!API_KEY) {
    console.warn('[propsApi] No ODDS_API_KEY set — skipping Vegas edge');
    return {};
  }

  const allEvents = [];
  for (const sport of PROP_SPORTS) {
    try {
      const { data } = await axios.get(`${BASE_URL}/sports/${sport}/odds`, {
        params: { apiKey: API_KEY, regions: 'us', markets: PROP_MARKETS, oddsFormat: 'american' },
        timeout: 15000,
      });
      allEvents.push(...(data ?? []));
      console.log(`[propsApi] ${sport}: ${data?.length ?? 0} events`);
    } catch (err) {
      console.warn(`[propsApi] ${sport} failed:`, err.response?.status ?? err.message);
      // Return cached or empty map on API error instead of crashing
      if (err.code === 'ECONNABORTED' || err.response?.status === 429) {
        console.warn('[propsApi] Rate limit or timeout — returning empty props');
        return {};
      }
    }
  }

  const map = buildPropMap(allEvents);
  console.log(`[propsApi] Prop map: ${Object.keys(map).length} lines from ${allEvents.length} events`);
  cache.set(cacheKey, map, CACHE_TTL);
  return map;
}

module.exports = { fetchPlayerProps, normalizeName };
