require('dotenv').config();
const axios = require('axios');

const PUBLIC_URL = 'https://api.elections.kalshi.com/trade-api/v2';

async function run() {
  const { data } = await axios.get(`${PUBLIC_URL}/markets`, {
    params: { status: 'open', limit: 200 },
    timeout: 10000,
  });

  // Find a sports bundle market
  const market = (data?.markets || []).find(m =>
    (m.title || '').toLowerCase().includes('brown') ||
    (m.title || '').toLowerCase().includes('tatum')
  );

  if (!market) { console.log('No market found'); return; }

  console.log('BUNDLE TICKER:', market.ticker);
  console.log('BUNDLE TITLE:', market.title);
  console.log('');

  // Fetch ALL sub-market details
  const legs = market.mve_selected_legs || [];
  console.log(`Fetching ${legs.length} sub-markets...\n`);

  for (const leg of legs.slice(0, 6)) {
    const { data: sub } = await axios.get(`${PUBLIC_URL}/markets/${leg.market_ticker}`, { timeout: 8000 });
    const m = sub?.market || sub;
    console.log('--- SUB MARKET ---');
    console.log('ticker:', leg.market_ticker);
    console.log('title:', m?.title);
    console.log('subtitle:', m?.subtitle);
    console.log('rules_primary:', m?.rules_primary?.slice(0, 200));
    console.log('');
  }
}

run().catch(e => console.error('ERROR:', e.response?.data ?? e.message));
