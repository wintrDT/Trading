require('dotenv').config();
const axios = require('axios');

const PUBLIC_URL = 'https://api.elections.kalshi.com/trade-api/v2';

async function run() {
  // Fetch more markets and look for ones with actual prices
  const { data } = await axios.get(`${PUBLIC_URL}/markets`, {
    params: { status: 'open', limit: 100 },
    timeout: 10000,
  });

  const all = data?.markets || [];
  console.log(`Total: ${all.length} markets\n`);

  // Find markets with non-zero prices
  const priced = all.filter(m => {
    const yb = parseFloat(m.yes_bid_dollars ?? 0);
    const ya = parseFloat(m.yes_ask_dollars ?? 0);
    const lp = parseFloat(m.last_price_dollars ?? 0);
    return yb > 0 || ya > 0 || lp > 0;
  });

  console.log(`Markets with actual prices: ${priced.length}`);
  priced.slice(0, 10).forEach(m => {
    console.log(`\n[${m.ticker?.slice(0,40)}]`);
    console.log(`  Title: ${m.title?.slice(0,60)}`);
    console.log(`  yes_bid: ${m.yes_bid_dollars} | yes_ask: ${m.yes_ask_dollars} | last: ${m.last_price_dollars}`);
    console.log(`  volume: ${m.volume_24h_fp} | type: ${m.market_type}`);
  });

  // Show ticker prefixes of priced markets
  const prefixes = [...new Set(priced.map(m => m.ticker?.slice(0,8)))];
  console.log('\nTicker prefixes with prices:', prefixes);

  // Try fetching page 2
  console.log('\n--- Fetching with cursor for more results ---');
  if (data.cursor) {
    const { data: data2 } = await axios.get(`${PUBLIC_URL}/markets`, {
      params: { status: 'open', limit: 100, cursor: data.cursor },
      timeout: 10000,
    });
    const all2 = data2?.markets || [];
    const priced2 = all2.filter(m => parseFloat(m.yes_bid_dollars ?? 0) > 0 || parseFloat(m.yes_ask_dollars ?? 0) > 0);
    console.log(`Page 2: ${all2.length} markets, ${priced2.length} priced`);
    priced2.slice(0, 5).forEach(m => {
      console.log(`  [${m.ticker?.slice(0,40)}] bid:${m.yes_bid_dollars} ask:${m.yes_ask_dollars}`);
    });
  }
}

run().catch(e => console.error('ERROR:', e.response?.data ?? e.message));
