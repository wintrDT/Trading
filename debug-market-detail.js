require('dotenv').config();
const axios = require('axios');

const PUBLIC_URL = 'https://api.elections.kalshi.com/trade-api/v2';

async function run() {
  // Fetch markets and find a sports one with player names
  const { data } = await axios.get(`${PUBLIC_URL}/markets`, {
    params: { status: 'open', limit: 200 },
    timeout: 10000,
  });

  const all = data?.markets || [];
  
  // Find one with player names
  const sports = all.filter(m => 
    (m.title || '').toLowerCase().includes('elly') ||
    (m.title || '').toLowerCase().includes('tatum') ||
    (m.title || '').toLowerCase().includes('brown')
  );

  if (!sports.length) { console.log('No matching markets found'); return; }
  
  const sample = sports[0];
  console.log('=== SAMPLE MARKET ===');
  console.log('ticker:', sample.ticker);
  console.log('title:', sample.title);
  console.log('rules_primary:', sample.rules_primary);
  console.log('rules_secondary:', sample.rules_secondary);
  console.log('category:', sample.category);
  console.log('subtitle:', sample.subtitle);
  console.log('no_sub_title:', sample.no_sub_title);
  console.log('event_ticker:', sample.event_ticker);
  console.log('mve_selected_legs (first 2):');
  (sample.mve_selected_legs || []).slice(0, 2).forEach(leg => console.log(' ', JSON.stringify(leg)));

  // Fetch the full market detail
  console.log('\n=== FETCHING FULL MARKET DETAIL ===');
  const { data: detail } = await axios.get(`${PUBLIC_URL}/markets/${sample.ticker}`, { timeout: 8000 });
  const m = detail?.market || detail;
  console.log('rules_primary:', m.rules_primary);
  console.log('subtitle:', m.subtitle);
  
  // Fetch the event to get sub-market titles
  if (sample.event_ticker) {
    console.log('\n=== FETCHING EVENT ===');
    try {
      const { data: event } = await axios.get(`${PUBLIC_URL}/events/${sample.event_ticker}`, { timeout: 8000 });
      console.log('event title:', event?.event?.title);
      console.log('markets in event:');
      (event?.event?.markets || []).slice(0, 3).forEach(em => {
        console.log(`  [${em.ticker}] ${em.title} | subtitle: ${em.subtitle}`);
      });
    } catch(e) { console.log('Event fetch failed:', e.message); }
  }

  // Fetch one of the mve sub-market tickers directly
  const legs = sample.mve_selected_legs || [];
  if (legs.length > 0) {
    console.log('\n=== FETCHING SUB-MARKET ===', legs[0].market_ticker);
    try {
      const { data: sub } = await axios.get(`${PUBLIC_URL}/markets/${legs[0].market_ticker}`, { timeout: 8000 });
      const sm = sub?.market || sub;
      console.log('sub title:', sm.title);
      console.log('sub subtitle:', sm.subtitle);
      console.log('sub rules_primary:', sm.rules_primary);
      console.log('sub category:', sm.category);
    } catch(e) { console.log('Sub-market fetch failed:', e.message); }
  }
}

run().catch(e => console.error('ERROR:', e.response?.data ?? e.message));
