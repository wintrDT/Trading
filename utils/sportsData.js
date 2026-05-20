// utils/sportsData.js
// Real-world context for parlay scoring: injuries, team form, player season averages.
// Supports both NBA and MLB. All data is cached aggressively so generateParlays
// doesn't hammer external APIs.

const axios  = require('axios');
const cache  = require('./cache');
const { normalizeName } = require('./propsApi');

const ESPN      = 'https://site.api.espn.com/apis';
const ESPN_CORE = 'https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba';
const ESPN_MLB_CORE = 'https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb';
const HEADS     = { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' };

// ════════════════════════════════════════════════════════════
//  NBA
// ════════════════════════════════════════════════════════════

// ── Injuries ──────────────────────────────────────────────────────────────────
async function getInjuredPlayers() {
  if (cache.has('injuries_nba')) return cache.get('injuries_nba');

  try {
    const { data } = await axios.get(
      `${ESPN}/site/v2/sports/basketball/nba/injuries`,
      { timeout: 8000, headers: HEADS }
    );
    const map = new Map();
    for (const item of (data?.injuries || [])) {
      const name = item.athlete?.displayName || item.name;
      const raw  = (item.status || '').toLowerCase();
      if (!name || !raw) continue;
      let status;
      if (/out|inactive/i.test(raw))       status = 'out';
      else if (/doubtful/i.test(raw))      status = 'doubtful';
      else if (/questionable/i.test(raw))  status = 'questionable';
      else if (/day.?to.?day/i.test(raw))  status = 'dtd';
      if (status) map.set(normalizeName(name), status);
    }
    cache.set('injuries_nba', map, 30 * 60);
    console.log(`[sportsData] NBA injuries: ${map.size}`);
    return map;
  } catch (err) {
    console.warn('[sportsData] NBA injuries fetch failed:', err.message);
    return new Map();
  }
}

// ── Team records ──────────────────────────────────────────────────────────────
async function getTeamRecords() {
  if (cache.has('team_records_nba')) return cache.get('team_records_nba');

  try {
    const { data } = await axios.get(
      `https://site.api.espn.com/apis/v2/sports/basketball/nba/standings`,
      { timeout: 8000, headers: HEADS }
    );
    const map = new Map();
    for (const conf of (data?.children || [])) {
      for (const entry of (conf?.standings?.entries || [])) {
        const abbr = entry.team?.abbreviation?.toUpperCase();
        if (!abbr) continue;
        const sv     = name => entry.stats?.find(s => s.name === name)?.value ?? 0;
        const wins   = sv('wins');
        const losses = sv('losses');
        map.set(abbr, {
          wins, losses,
          homeWins:   sv('homeWins')   || sv('Home Wins'),
          homeLosses: sv('homeLosses') || sv('Home Losses'),
          awayWins:   sv('awayWins')   || sv('Away Wins'),
          awayLosses: sv('awayLosses') || sv('Away Losses'),
          winPct:     wins + losses > 0 ? wins / (wins + losses) : 0.5,
        });
      }
    }
    const ALIAS = { GS: 'GSW', SA: 'SAS', NO: 'NOP', NY: 'NYK', PHX: 'PHO', WSH: 'WAS' };
    for (const [from, to] of Object.entries(ALIAS)) {
      if (!map.has(to) && map.has(from)) map.set(to, map.get(from));
      if (!map.has(from) && map.has(to)) map.set(from, map.get(to));
    }
    cache.set('team_records_nba', map, 60 * 60);
    console.log(`[sportsData] NBA team records: ${map.size}`);
    return map;
  } catch (err) {
    console.warn('[sportsData] NBA team records fetch failed:', err.message);
    return new Map();
  }
}

// ── Back-to-back detection ────────────────────────────────────────────────────
async function getBackToBackTeams() {
  if (cache.has('nba_b2b_teams')) return cache.get('nba_b2b_teams');

  try {
    const today     = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const fmt = d => `${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}`;

    const [todayRes, yestRes] = await Promise.all([
      axios.get(`${ESPN}/site/v2/sports/basketball/nba/scoreboard?dates=${fmt(today)}`, { timeout: 8000, headers: HEADS }),
      axios.get(`${ESPN}/site/v2/sports/basketball/nba/scoreboard?dates=${fmt(yesterday)}`, { timeout: 8000, headers: HEADS }),
    ]);

    const teamsOnDate = d => {
      const s = new Set();
      for (const ev of (d?.events || [])) {
        for (const comp of (ev.competitions || [])) {
          for (const c of (comp.competitors || [])) {
            const a = c.team?.abbreviation?.toUpperCase();
            if (a) s.add(a);
          }
        }
      }
      return s;
    };

    const todayTeams     = teamsOnDate(todayRes.data);
    const yesterdayTeams = teamsOnDate(yestRes.data);
    const b2bTeams       = new Set([...todayTeams].filter(t => yesterdayTeams.has(t)));

    const ALIAS = { GS: 'GSW', SA: 'SAS', NO: 'NOP', NY: 'NYK', PHX: 'PHO', WSH: 'WAS' };
    for (const [from, to] of Object.entries(ALIAS)) {
      if (b2bTeams.has(from)) b2bTeams.add(to);
      if (b2bTeams.has(to))   b2bTeams.add(from);
    }

    console.log(`[sportsData] NBA B2B teams: ${[...b2bTeams].join(', ') || 'none'}`);
    cache.set('nba_b2b_teams', b2bTeams, 2 * 60 * 60);
    return b2bTeams;
  } catch (err) {
    console.warn('[sportsData] NBA B2B fetch failed:', err.message);
    return new Set();
  }
}

// ── Player ID map ─────────────────────────────────────────────────────────────
const NBA_TEAM_ABBRS = [
  'ATL','BOS','BKN','CHA','CHI','CLE','DAL','DEN','DET','GSW',
  'HOU','IND','LAC','LAL','MEM','MIA','MIL','MIN','NOP','NYK',
  'OKC','ORL','PHI','PHO','POR','SAC','SAS','TOR','UTA','WAS',
];

async function buildPlayerIdMap() {
  if (cache.has('nba_player_id_map')) return cache.get('nba_player_id_map');

  const map = new Map();
  const rosters = await Promise.allSettled(
    NBA_TEAM_ABBRS.map(abbr =>
      axios.get(`${ESPN}/site/v2/sports/basketball/nba/teams/${abbr}/roster`,
        { timeout: 10000, headers: HEADS })
        .then(r => ({ abbr, data: r.data }))
    )
  );

  for (const r of rosters) {
    if (r.status !== 'fulfilled') continue;
    const { abbr, data } = r.value;
    for (const group of (data?.athletes || [])) {
      const items = Array.isArray(group?.items) ? group.items : [group];
      for (const a of items) {
        const id   = a?.id || a?.athlete?.id;
        const name = a?.fullName || a?.displayName || a?.athlete?.fullName || a?.athlete?.displayName;
        if (id && name) map.set(normalizeName(name), { id: String(id), team: abbr });
      }
    }
  }

  console.log(`[sportsData] built NBA player ID map: ${map.size} players`);
  cache.set('nba_player_id_map', map, 24 * 60 * 60);
  return map;
}

const ESPN_STAT_MAP = {
  avgPoints:                    'pts',
  avgAssists:                   'ast',
  avgRebounds:                  'reb',
  avgThreePointFieldGoalsMade:  'fg3m',
  avgSteals:                    'stl',
  avgBlocks:                    'blk',
  avgMinutes:                   'minutes',
};

async function getPlayerSeasonAvg(playerName) {
  const norm     = normalizeName(playerName);
  const cacheKey = `player_avg_${norm}`;
  if (cache.has(cacheKey)) return cache.get(cacheKey);

  try {
    const idMap      = await buildPlayerIdMap();
    const playerInfo = idMap.get(norm);
    const athleteId  = playerInfo?.id || playerInfo;

    if (athleteId) {
      const { data } = await axios.get(
        `${ESPN_CORE}/athletes/${athleteId}/statistics/0`,
        { timeout: 8000, headers: HEADS }
      );
      const categories = data?.splits?.categories || [];
      const avgs = {};
      for (const cat of categories) {
        for (const s of (cat.stats || [])) {
          const field = ESPN_STAT_MAP[s.name];
          if (field && avgs[field] == null) avgs[field] = parseFloat(s.value ?? 0);
        }
      }
      if (avgs.pts != null || avgs.ast != null) {
        if (playerInfo?.team) avgs.team = playerInfo.team;
        cache.set(cacheKey, avgs, 60 * 60);
        return avgs;
      }
    }
  } catch (err) {
    console.warn(`[sportsData] NBA season avg failed for ${playerName}:`, err.message);
  }

  cache.set(cacheKey, null, 10 * 60);
  return null;
}

async function getPlayerRecentAvg(playerName, lastN = 5) {
  const norm     = normalizeName(playerName);
  const cacheKey = `player_recent_${norm}_${lastN}`;
  if (cache.has(cacheKey)) return cache.get(cacheKey);

  try {
    const idMap      = await buildPlayerIdMap();
    const playerInfo = idMap.get(norm);
    const athleteId  = playerInfo?.id || playerInfo;
    if (!athleteId) { cache.set(cacheKey, null, 10 * 60); return null; }

    const logRes = await axios.get(
      `${ESPN_CORE}/athletes/${athleteId}/eventlog`,
      { timeout: 10000, headers: HEADS }
    );

    const items      = logRes.data?.events?.items || [];
    const playedItems = items.filter(i => i.played !== false).slice(0, lastN);
    if (!playedItems.length) { cache.set(cacheKey, null, 10 * 60); return null; }

    const statResults = await Promise.allSettled(
      playedItems.map(item => {
        const statsUrl = item.statistics?.$ref;
        if (!statsUrl) return Promise.resolve(null);
        return axios.get(statsUrl.replace('http://', 'https://'), { timeout: 6000, headers: HEADS });
      })
    );

    const gameStats = [];
    for (const r of statResults) {
      if (r.status !== 'fulfilled' || !r.value) continue;
      const cats = r.value.data?.splits?.categories || [];
      const game = {};
      for (const cat of cats) {
        for (const s of (cat.stats || [])) {
          const field = ESPN_STAT_MAP[s.name];
          if (field && game[field] == null) game[field] = parseFloat(s.value ?? 0);
        }
      }
      if (Object.keys(game).length > 0) gameStats.push(game);
    }

    if (!gameStats.length) { cache.set(cacheKey, null, 10 * 60); return null; }

    const avg = field => +(gameStats.reduce((sum, g) => sum + (g[field] ?? 0), 0) / gameStats.length).toFixed(1);

    const result = {
      pts: avg('pts'), ast: avg('ast'), reb: avg('reb'),
      fg3m: avg('fg3m'), stl: avg('stl'), blk: avg('blk'),
      minutes: avg('minutes'), gp: gameStats.length,
    };

    cache.set(cacheKey, result, 60 * 60);
    return result;
  } catch (err) {
    console.warn(`[sportsData] NBA recent avg failed for ${playerName}:`, err.message);
    cache.set(cacheKey, null, 10 * 60);
    return null;
  }
}

// ════════════════════════════════════════════════════════════
//  MLB
// ════════════════════════════════════════════════════════════

const MLB_TEAM_ABBRS = [
  'ARI','ATL','BAL','BOS','CHC','CWS','CIN','CLE','COL','DET',
  'HOU','KC','LAA','LAD','MIA','MIL','MIN','NYM','NYY','OAK',
  'PHI','PIT','SD','SEA','SF','STL','TB','TEX','TOR','WSH',
];

// ESPN returns different stat key names across sports and endpoints.
// We cast everything we care about to a common internal field name.
const MLB_ESPN_STAT_MAP = {
  // Batting — per game values from event log
  hits:               'hits',
  avgHits:            'hits',
  homeRuns:           'hr',
  avgHomeRuns:        'hr',
  homeRun:            'hr',
  RBI:                'rbi',
  rbi:                'rbi',
  RBIs:               'rbi',
  avgRBI:             'rbi',
  avgRBIs:            'rbi',
  // Strikeouts — pitcher prop (KXMLBSO); same key for batter Ks, context decides
  strikeouts:         'so',
  avgStrikeouts:      'so',
  strikeoutsSwinging: 'so',
  // Walks
  walks:              'bb',
  avgWalks:           'bb',
  baseOnBalls:        'bb',
  // Batting average (season context only)
  avg:                'avg',
  battingAverage:     'avg',
  // Games played / at-bats (for activity gate)
  gamesPlayed:        'gp',
  atBats:             'ab',
};

// ── MLB injuries ──────────────────────────────────────────────────────────────
async function getMLBInjuredPlayers() {
  if (cache.has('injuries_mlb')) return cache.get('injuries_mlb');

  try {
    const { data } = await axios.get(
      `${ESPN}/site/v2/sports/baseball/mlb/injuries`,
      { timeout: 8000, headers: HEADS }
    );
    const map = new Map();
    for (const item of (data?.injuries || [])) {
      const name = item.athlete?.displayName || item.name;
      const raw  = (item.status || '').toLowerCase();
      if (!name || !raw) continue;
      let status;
      if (/out|inactive/i.test(raw))       status = 'out';
      else if (/doubtful/i.test(raw))      status = 'doubtful';
      else if (/questionable/i.test(raw))  status = 'questionable';
      else if (/day.?to.?day/i.test(raw))  status = 'dtd';
      if (status) map.set(normalizeName(name), status);
    }
    cache.set('injuries_mlb', map, 30 * 60);
    console.log(`[sportsData] MLB injuries: ${map.size}`);
    return map;
  } catch (err) {
    console.warn('[sportsData] MLB injuries fetch failed:', err.message);
    return new Map();
  }
}

// ── MLB player ID map (all 30 team rosters) ───────────────────────────────────
async function buildMLBPlayerIdMap() {
  if (cache.has('mlb_player_id_map')) return cache.get('mlb_player_id_map');

  const map = new Map();
  const rosters = await Promise.allSettled(
    MLB_TEAM_ABBRS.map(abbr =>
      axios.get(`${ESPN}/site/v2/sports/baseball/mlb/teams/${abbr}/roster`,
        { timeout: 10000, headers: HEADS })
        .then(r => ({ abbr, data: r.data }))
    )
  );

  for (const r of rosters) {
    if (r.status !== 'fulfilled') continue;
    const { abbr, data } = r.value;
    for (const group of (data?.athletes || [])) {
      const items = Array.isArray(group?.items) ? group.items : [group];
      for (const a of items) {
        const id   = a?.id || a?.athlete?.id;
        const name = a?.fullName || a?.displayName || a?.athlete?.fullName || a?.athlete?.displayName;
        if (id && name) map.set(normalizeName(name), { id: String(id), team: abbr });
      }
    }
  }

  console.log(`[sportsData] built MLB player ID map: ${map.size} players`);
  cache.set('mlb_player_id_map', map, 24 * 60 * 60);
  return map;
}

// ── MLB season averages ───────────────────────────────────────────────────────
// Returns { hits, hr, rbi, so, bb, avg, gp, team } or null
async function getMLBPlayerSeasonAvg(playerName) {
  const norm     = normalizeName(playerName);
  const cacheKey = `mlb_player_avg_${norm}`;
  if (cache.has(cacheKey)) return cache.get(cacheKey);

  try {
    const idMap      = await buildMLBPlayerIdMap();
    const playerInfo = idMap.get(norm);
    const athleteId  = playerInfo?.id;

    if (athleteId) {
      const { data } = await axios.get(
        `${ESPN_MLB_CORE}/athletes/${athleteId}/statistics/0`,
        { timeout: 8000, headers: HEADS }
      );
      const categories = data?.splits?.categories || [];
      const raw = {};
      for (const cat of categories) {
        for (const s of (cat.stats || [])) {
          const field = MLB_ESPN_STAT_MAP[s.name];
          if (field && raw[field] == null) raw[field] = parseFloat(s.value ?? 0);
        }
      }

      // Convert season totals to per-game averages where gp is available
      const gp = raw.gp || 1;
      const avgs = { ...raw };
      if (raw.hits != null)  avgs.hits = +(raw.hits  / gp).toFixed(2);
      if (raw.hr   != null)  avgs.hr   = +(raw.hr    / gp).toFixed(2);
      if (raw.rbi  != null)  avgs.rbi  = +(raw.rbi   / gp).toFixed(2);
      if (raw.so   != null)  avgs.so   = +(raw.so    / gp).toFixed(2);
      if (raw.bb   != null)  avgs.bb   = +(raw.bb    / gp).toFixed(2);

      if (Object.keys(avgs).length > 0) {
        if (playerInfo?.team) avgs.team = playerInfo.team;
        cache.set(cacheKey, avgs, 60 * 60);
        console.log(`[sportsData] MLB season avg ${playerName}: ${avgs.hits}H ${avgs.hr}HR ${avgs.rbi}RBI ${avgs.so}K/g`);
        return avgs;
      }
    }
  } catch (err) {
    console.warn(`[sportsData] MLB season avg failed for ${playerName}:`, err.message);
  }

  cache.set(cacheKey, null, 10 * 60);
  return null;
}

// ── MLB recent form (last 5 games) ────────────────────────────────────────────
// Returns { hits, hr, rbi, so, bb, gp, team } or null
async function getMLBPlayerRecentAvg(playerName, lastN = 5) {
  const norm     = normalizeName(playerName);
  const cacheKey = `mlb_player_recent_${norm}_${lastN}`;
  if (cache.has(cacheKey)) return cache.get(cacheKey);

  try {
    const idMap      = await buildMLBPlayerIdMap();
    const playerInfo = idMap.get(norm);
    const athleteId  = playerInfo?.id;
    if (!athleteId) { cache.set(cacheKey, null, 10 * 60); return null; }

    const logRes = await axios.get(
      `${ESPN_MLB_CORE}/athletes/${athleteId}/eventlog`,
      { timeout: 10000, headers: HEADS }
    );

    const items      = logRes.data?.events?.items || [];
    const playedItems = items.filter(i => i.played !== false).slice(0, lastN);
    if (!playedItems.length) { cache.set(cacheKey, null, 10 * 60); return null; }

    const statResults = await Promise.allSettled(
      playedItems.map(item => {
        const statsUrl = item.statistics?.$ref;
        if (!statsUrl) return Promise.resolve(null);
        return axios.get(statsUrl.replace('http://', 'https://'), { timeout: 6000, headers: HEADS });
      })
    );

    const gameStats = [];
    for (const r of statResults) {
      if (r.status !== 'fulfilled' || !r.value) continue;
      const cats = r.value.data?.splits?.categories || [];
      const game = {};
      for (const cat of cats) {
        for (const s of (cat.stats || [])) {
          const field = MLB_ESPN_STAT_MAP[s.name];
          if (field && game[field] == null) game[field] = parseFloat(s.value ?? 0);
        }
      }
      if (Object.keys(game).length > 0) gameStats.push(game);
    }

    if (!gameStats.length) { cache.set(cacheKey, null, 10 * 60); return null; }

    const avg = field => +(gameStats.reduce((sum, g) => sum + (g[field] ?? 0), 0) / gameStats.length).toFixed(2);

    const result = {
      hits: avg('hits'),
      hr:   avg('hr'),
      rbi:  avg('rbi'),
      so:   avg('so'),
      bb:   avg('bb'),
      gp:   gameStats.length,
    };
    if (playerInfo?.team) result.team = playerInfo.team;

    cache.set(cacheKey, result, 60 * 60);
    console.log(`[sportsData] MLB L${lastN} ${playerName}: ${result.hits}H ${result.hr}HR ${result.rbi}RBI ${result.so}K`);
    return result;
  } catch (err) {
    console.warn(`[sportsData] MLB recent avg failed for ${playerName}:`, err.message);
    cache.set(cacheKey, null, 10 * 60);
    return null;
  }
}

// ════════════════════════════════════════════════════════════
//  Shared ticker utilities
// ════════════════════════════════════════════════════════════

const TICKER_TO_AVG_FIELD = {
  // NBA
  KXNBAPTS:   'pts',
  KXNBAAST:   'ast',
  KXNBAREB:   'reb',
  KXNBATHRPM: 'fg3m',
  KXNBASTL:   'stl',
  KXNBABLK:   'blk',
  // MLB
  KXMLBHITS:  'hits',
  KXMLBHR:    'hr',
  KXMLBRBI:   'rbi',
  KXMLBSO:    'so',
  KXMLBBB:    'bb',
};

function avgFieldFromTicker(ticker) {
  const t = (ticker || '').toUpperCase();
  for (const [prefix, field] of Object.entries(TICKER_TO_AVG_FIELD)) {
    if (t.startsWith(prefix)) return field;
  }
  return null;
}

function isMLBTicker(ticker) {
  return (ticker || '').toUpperCase().startsWith('KXMLB');
}

// ── Pre-fetch all context for a set of markets ────────────────────────────────
// Returns { injuries, mlbInjuries, teamRecords, playerAvgs, recentAvgs, b2bTeams }
async function fetchSportsContext(markets, expandNameFn) {
  const nbaNames = new Set();
  const mlbNames = new Set();

  for (const m of markets) {
    const field = avgFieldFromTicker(m.ticker || '');
    if (!field) continue;
    const raw = m.no_sub_title || m.title || '';
    const pm  = raw.match(/^([^:,0-9][^:]{2,}):\s*[\d.]+\+/);
    if (!pm) continue;
    const name = expandNameFn ? expandNameFn(pm[1].trim()) : pm[1].trim();
    if (!name || name.length <= 3) continue;
    if (isMLBTicker(m.ticker)) mlbNames.add(name);
    else                        nbaNames.add(name);
  }

  const [injuries, mlbInjuries, teamRecords, b2bTeams] = await Promise.all([
    getInjuredPlayers().catch(() => new Map()),
    getMLBInjuredPlayers().catch(() => new Map()),
    getTeamRecords().catch(() => new Map()),
    getBackToBackTeams().catch(() => new Set()),
  ]);

  // Season avgs + recent form — batched in groups of 4
  const playerAvgs = new Map();
  const recentAvgs = new Map();

  // NBA
  const nbaArr = [...nbaNames];
  for (let i = 0; i < nbaArr.length; i += 4) {
    const batch = nbaArr.slice(i, i + 4);
    const [seasonRes, recentRes] = await Promise.all([
      Promise.allSettled(batch.map(n => getPlayerSeasonAvg(n))),
      Promise.allSettled(batch.map(n => getPlayerRecentAvg(n, 5))),
    ]);
    seasonRes.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value) playerAvgs.set(normalizeName(nbaArr[i + idx]), r.value);
    });
    recentRes.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value) recentAvgs.set(normalizeName(nbaArr[i + idx]), r.value);
    });
  }

  // MLB
  const mlbArr = [...mlbNames];
  for (let i = 0; i < mlbArr.length; i += 4) {
    const batch = mlbArr.slice(i, i + 4);
    const [seasonRes, recentRes] = await Promise.all([
      Promise.allSettled(batch.map(n => getMLBPlayerSeasonAvg(n))),
      Promise.allSettled(batch.map(n => getMLBPlayerRecentAvg(n, 5))),
    ]);
    seasonRes.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value) playerAvgs.set(normalizeName(mlbArr[i + idx]), r.value);
    });
    recentRes.forEach((r, idx) => {
      if (r.status === 'fulfilled' && r.value) recentAvgs.set(normalizeName(mlbArr[i + idx]), r.value);
    });
  }

  console.log(`[sportsData] context — NBA: ${nbaArr.length} players, MLB: ${mlbArr.length} players, avgs: ${playerAvgs.size}, recent: ${recentAvgs.size}`);
  return { injuries, mlbInjuries, teamRecords, playerAvgs, recentAvgs, b2bTeams };
}

module.exports = {
  getInjuredPlayers,
  getMLBInjuredPlayers,
  getTeamRecords,
  getBackToBackTeams,
  getPlayerSeasonAvg,
  getPlayerRecentAvg,
  getMLBPlayerSeasonAvg,
  getMLBPlayerRecentAvg,
  avgFieldFromTicker,
  isMLBTicker,
  fetchSportsContext,
};
