// utils/statsApi.js
// Player & team stats via ESPN unofficial API (no key needed) + BallDontLie (NBA)

const axios = require('axios');
const cache = require('./cache');

const ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports';
const BDL_BASE  = 'https://api.balldontlie.io/v1';
const BDL_KEY   = process.env.BALLDONTLIE_API_KEY || '';

// Map sport choices to ESPN endpoints
const ESPN_SPORT_MAP = {
  nba:   { sport: 'basketball', league: 'nba',           name: 'NBA',           emoji: '🏀' },
  nfl:   { sport: 'football',   league: 'nfl',           name: 'NFL',           emoji: '🏈' },
  mlb:   { sport: 'baseball',   league: 'mlb',           name: 'MLB',           emoji: '⚾' },
  ncaab: { sport: 'basketball', league: 'mens-college-basketball', name: "NCAA Men's", emoji: '🏀' },
  ncaaf: { sport: 'football',   league: 'college-football',       name: 'NCAA Football', emoji: '🏈' },
};

// ── ESPN: Search for a player ─────────────────────────────────────────────────
async function searchPlayerESPN(name, sportKey) {
  const cacheKey = `espn_player_${sportKey}_${name.toLowerCase().replace(/\s+/g, '_')}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const { sport, league } = ESPN_SPORT_MAP[sportKey];
  try {
    const { data } = await axios.get(
      `${ESPN_BASE}/${sport}/${league}/athletes`,
      { params: { limit: 10, search: name }, timeout: 8000 }
    );
    const athletes = data?.items || data?.athletes || [];
    cache.set(cacheKey, athletes, 60 * 60); // 1-hour cache
    return athletes;
  } catch (err) {
    console.error(`ESPN search error: ${err.message}`);
    return [];
  }
}

// ── ESPN: Get player stats by athlete ID ─────────────────────────────────────
async function getPlayerStatsESPN(athleteId, sportKey) {
  const cacheKey = `espn_stats_${sportKey}_${athleteId}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const { sport, league } = ESPN_SPORT_MAP[sportKey];
  try {
    const { data } = await axios.get(
      `${ESPN_BASE}/${sport}/${league}/athletes/${athleteId}/statistics`,
      { timeout: 8000 }
    );
    cache.set(cacheKey, data, 30 * 60);
    return data;
  } catch (err) {
    console.error(`ESPN stats error: ${err.message}`);
    return null;
  }
}

// ── ESPN: Get player overview (bio + current season) ─────────────────────────
async function getPlayerOverviewESPN(athleteId, sportKey) {
  const cacheKey = `espn_overview_${sportKey}_${athleteId}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const { sport, league } = ESPN_SPORT_MAP[sportKey];
  try {
    const { data } = await axios.get(
      `${ESPN_BASE}/${sport}/${league}/athletes/${athleteId}`,
      { timeout: 8000 }
    );
    cache.set(cacheKey, data, 30 * 60);
    return data;
  } catch (err) {
    console.error(`ESPN overview error: ${err.message}`);
    return null;
  }
}

// ── BallDontLie: NBA player search + season averages ─────────────────────────
async function getNBAStatsBDL(playerName) {
  const cacheKey = `bdl_${playerName.toLowerCase().replace(/\s+/g, '_')}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const headers = BDL_KEY ? { Authorization: BDL_KEY } : {};

  try {
    // Search for player
    const searchRes = await axios.get(`${BDL_BASE}/players`, {
      params: { search: playerName, per_page: 5 },
      headers, timeout: 8000,
    });

    const players = searchRes.data?.data || [];
    if (!players.length) return null;

    const player = players[0];

    // Get current season averages
    const currentSeason = new Date().getFullYear() - (new Date().getMonth() < 9 ? 1 : 0);
    const statsRes = await axios.get(`${BDL_BASE}/season_averages`, {
      params: { season: currentSeason, player_ids: [player.id] },
      headers, timeout: 8000,
    });

    const stats = statsRes.data?.data?.[0] || null;
    const result = { player, stats, season: currentSeason };
    cache.set(cacheKey, result, 30 * 60);
    return result;
  } catch (err) {
    console.error(`BallDontLie error: ${err.message}`);
    return null;
  }
}

// ── ESPN: Get team roster ─────────────────────────────────────────────────────
async function getTeamRosterESPN(teamName, sportKey) {
  const cacheKey = `espn_roster_${sportKey}_${teamName.toLowerCase().replace(/\s+/g, '_')}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const { sport, league } = ESPN_SPORT_MAP[sportKey];
  try {
    // First find the team
    const teamsRes = await axios.get(`${ESPN_BASE}/${sport}/${league}/teams`, {
      params: { limit: 32 }, timeout: 8000,
    });
    const teams = teamsRes.data?.sports?.[0]?.leagues?.[0]?.teams || [];
    const match = teams.find(t =>
      t.team.displayName.toLowerCase().includes(teamName.toLowerCase()) ||
      t.team.abbreviation.toLowerCase() === teamName.toLowerCase()
    );
    if (!match) return null;

    const teamId = match.team.id;
    const rosterRes = await axios.get(
      `${ESPN_BASE}/${sport}/${league}/teams/${teamId}/roster`,
      { timeout: 8000 }
    );
    const result = { team: match.team, roster: rosterRes.data };
    cache.set(cacheKey, result, 60 * 60);
    return result;
  } catch (err) {
    console.error(`ESPN roster error: ${err.message}`);
    return null;
  }
}

// ── ESPN: Get team stats ──────────────────────────────────────────────────────
async function getTeamStatsESPN(teamName, sportKey) {
  const cacheKey = `espn_teamstats_${sportKey}_${teamName.toLowerCase().replace(/\s+/g, '_')}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const { sport, league } = ESPN_SPORT_MAP[sportKey];
  try {
    const teamsRes = await axios.get(`${ESPN_BASE}/${sport}/${league}/teams`, {
      params: { limit: 32 }, timeout: 8000,
    });
    const teams = teamsRes.data?.sports?.[0]?.leagues?.[0]?.teams || [];
    const match = teams.find(t =>
      t.team.displayName.toLowerCase().includes(teamName.toLowerCase()) ||
      t.team.abbreviation.toLowerCase() === teamName.toLowerCase()
    );
    if (!match) return null;

    const teamId = match.team.id;
    const statsRes = await axios.get(
      `${ESPN_BASE}/${sport}/${league}/teams/${teamId}`,
      { params: { enable: 'stats' }, timeout: 8000 }
    );
    const result = { team: match.team, stats: statsRes.data };
    cache.set(cacheKey, result, 30 * 60);
    return result;
  } catch (err) {
    console.error(`ESPN team stats error: ${err.message}`);
    return null;
  }
}

module.exports = {
  searchPlayerESPN,
  getPlayerStatsESPN,
  getPlayerOverviewESPN,
  getNBAStatsBDL,
  getTeamRosterESPN,
  getTeamStatsESPN,
  ESPN_SPORT_MAP,
};
