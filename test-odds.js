// test-odds.js — run with: node test-odds.js
require('dotenv').config();
const axios = require('axios');

const API_KEY = process.env.ODDS_API_KEY;

console.log('─────────────────────────────────────');
console.log('ODDS_API_KEY present:', !!API_KEY);
console.log('Key preview:', API_KEY ? API_KEY.slice(0, 6) + '...' : 'MISSING');
console.log('─────────────────────────────────────\n');

if (!API_KEY) {
  console.error('❌ ODDS_API_KEY is not set in your .env file!');
  process.exit(1);
}

const SPORTS = [
  'basketball_nba',
  'americanfootball_nfl',
  'baseball_mlb',
  'basketball_ncaab',
  'basketball_ncaaw',
  'americanfootball_ncaaf',
];

async function testSport(sport) {
  try {
    const res = await axios.get(`https://api.the-odds-api.com/v4/sports/${sport}/odds`, {
      params: {
        apiKey: API_KEY,
        regions: 'us',
        markets: 'h2h',
        oddsFormat: 'american',
      },
      timeout: 10000,
    });

    const games = res.data;
    const remaining = res.headers['x-requests-remaining'] ?? '?';
    const used = res.headers['x-requests-used'] ?? '?';

    console.log(`✅ ${sport}`);
    console.log(`   Games found: ${games.length}`);
    console.log(`   API requests used: ${used} | remaining: ${remaining}`);

    if (games.length > 0) {
      const g = games[0];
      const gameTime = new Date(g.commence_time);
      const now = new Date();
      const hoursUntil = ((gameTime - now) / 1000 / 60 / 60).toFixed(1);
      console.log(`   Next game: ${g.away_team} @ ${g.home_team}`);
      console.log(`   Starts in: ${hoursUntil} hours`);
      console.log(`   Bookmakers: ${g.bookmakers?.length ?? 0}`);
    }
    console.log();
  } catch (err) {
    const status = err.response?.status;
    const msg = err.response?.data?.message ?? err.message;
    console.log(`❌ ${sport} — HTTP ${status ?? 'N/A'}: ${msg}\n`);
  }
}

(async () => {
  for (const sport of SPORTS) {
    await testSport(sport);
  }
})();
