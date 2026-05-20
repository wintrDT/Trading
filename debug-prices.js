require('dotenv').config();
const axios = require('axios');

const PUBLIC_URL = 'https://api.elections.kalshi.com/trade-api/v2';

async function run() {
  const { data } = await axios.get(`${PUBLIC_URL}/markets`, {
    params: { status: 'open', limit: 5 },
    timeout: 10000,
  });

  const markets = data?.markets || [];
  console.log(`Got ${markets.length} markets\n`);

  if (markets.length > 0) {
    const m = markets[0];
    console.log('=== ALL FIELDS ON FIRST MARKET ===');
    Object.entries(m).forEach(([k, v]) => {
      console.log(`  ${k}: ${JSON.stringify(v)}`);
    });

    console.log('\n=== PRICE-RELATED FIELDS (all 5 markets) ===');
    markets.forEach(m => {
      console.log(`\n[${m.ticker}] ${m.title?.slice(0,50)}`);
      const priceFields = ['yes_bid', 'yes_ask', 'no_bid', 'no_ask', 'last_price',
        'yes_price', 'no_price', 'last_yes_price', 'close_price', 'previous_yes_bid',
        'previous_yes_ask', 'previous_price', 'open_interest', 'volume', 'volume_24h'];
      priceFields.forEach(f => {
        if (m[f] !== undefined) console.log(`  ${f}: ${m[f]}`);
      });
    });
  }
}

run().catch(e => console.error('ERROR:', e.response?.data ?? e.message));
