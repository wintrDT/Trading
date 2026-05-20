// kalshi/sportsScores.js
// Fetches live game scores from ESPN's public scoreboard API (no key required).
// Cached 30s so refreshes stay snappy without hammering ESPN.
const axios = require('axios');

const LEAGUES = [
  { key: 'nba', name: 'NBA', url: 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard' },
  { key: 'nfl', name: 'NFL', url: 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard' },
  { key: 'mlb', name: 'MLB', url: 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard' },
  { key: 'nhl', name: 'NHL', url: 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard' },
];

let _cache    = null;
let _cacheAt  = 0;
const CACHE_TTL = 30 * 1000;

async function getLiveScores() {
  if (_cache && Date.now() - _cacheAt < CACHE_TTL) return _cache;

  const games = [];

  await Promise.allSettled(
    LEAGUES.map(async ({ key, name, url }) => {
      try {
        const { data } = await axios.get(url, {
          timeout: 8000,
          headers: { 'User-Agent': 'Mozilla/5.0' },
        });
        for (const event of data?.events || []) {
          const comp = event.competitions?.[0];
          if (!comp) continue;

          const state = comp.status?.type?.state; // 'pre' | 'in' | 'post'
          const competitors = comp.competitors || [];
          if (competitors.length < 2) continue;

          const teams = competitors.map(c => ({
            abbr:      (c.team?.abbreviation || '').toUpperCase(),
            shortName: c.team?.shortDisplayName || c.team?.name || '',
            name:      c.team?.displayName || '',
            score:     parseInt(c.score) || 0,
            isHome:    c.homeAway === 'home',
            winner:    c.winner || false,
          }));

          const home = teams.find(t => t.isHome)  || teams[1];
          const away = teams.find(t => !t.isHome) || teams[0];

          games.push({
            league:       key,
            leagueName:   name,
            id:           event.id,
            isLive:       state === 'in',
            isScheduled:  state === 'pre',
            isFinal:      state === 'post',
            statusDetail: comp.status?.type?.detail || '',
            clock:        comp.status?.displayClock || '',
            period:       comp.status?.period || 0,
            home,
            away,
          });
        }
      } catch (err) {
        console.warn(`[sportsScores] ${key} failed:`, err.message);
      }
    })
  );

  _cache   = games;
  _cacheAt = Date.now();
  console.log(`[sportsScores] ${games.length} games (${games.filter(g => g.isLive).length} live)`);
  return games;
}

module.exports = { getLiveScores };
