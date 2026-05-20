const router              = require('express').Router();
const { requireAuth }     = require('../middleware');
const { getStats, loadPicks, deletePick, updatePickResult } = require('../../utils/pickTracker');
const { generateParlays } = require('../../kalshi/parlayService');
const { getSportsMarkets, getGameMarkets } = require('../../kalshi/kalshiClient');
const { findUpsetMarkets, findHotMarkets } = require('../../kalshi/upsetService');
const { getLiveScores }   = require('../../kalshi/sportsScores');
const { getYesPrice }     = require('../../kalshi/kalshiAnalyzer');

// ── Helpers for ESPN ↔ Kalshi matching ────────────────────────────────────────
// Extract [TEAM1, TEAM2] from a Kalshi game ticker like KXNBAGAME-26APR30BOSPHI-PHI
function extractTeams(ticker) {
  const m = ticker.match(/KX(?:NBA|NFL|MLB|NHL)GAME-\d{2}[A-Z]{3}\d{2}([A-Z]{3})([A-Z]{3})/i);
  return m ? [m[1].toUpperCase(), m[2].toUpperCase()] : null;
}

// ESPN uses shorter codes for a handful of teams; map to Kalshi's 3-letter form
const ESPN_ALT = { SA: 'SAS', GS: 'GSW', NO: 'NOP', NY: 'NYK', WSH: 'WAS', PHX: 'PHO' };

function matchKalshiToGame(game, kalshiMarkets) {
  const h = game.home?.abbr;
  const a = game.away?.abbr;
  if (!h || !a) return [];
  const hOpts = [h, ESPN_ALT[h]].filter(Boolean);
  const aOpts = [a, ESPN_ALT[a]].filter(Boolean);
  return kalshiMarkets.filter(m => {
    const teams = extractTeams(m.ticker);
    return teams && hOpts.some(x => teams.includes(x)) && aOpts.some(x => teams.includes(x));
  });
}

// Health check for API
router.get('/api/health', (req, res) => {
  res.json({ status: 'ok', authenticated: !!req.session.user });
});

router.get('/', requireAuth, (req, res) => {
  const stats  = getStats();
  const picks  = loadPicks();

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  });

  const todayPicks = picks.filter(p => p.date === today);
  const recent     = picks.filter(p => p.result !== null).slice(-30).reverse();

  // Build cumulative P&L series for chart
  const pnlSeries = [];
  let running = 0;
  for (const p of picks.filter(p => p.result !== null).sort((a, b) => a.postedAt.localeCompare(b.postedAt))) {
    running += p.result === 'win'
      ? parseFloat(p.totalPayout) - parseFloat(p.totalCost)
      : -parseFloat(p.totalCost);
    pnlSeries.push({ date: p.date, value: parseFloat(running.toFixed(2)) });
  }

  res.render('dashboard', {
    user: req.session.user,
    stats,
    todayPicks,
    recent,
    today,
    pnlSeries: JSON.stringify(pnlSeries),
  });
});

// Debug: show price fields and which markets are actually priced
router.get('/api/debug/markets', requireAuth, async (req, res) => {
  try {
    const { getSportsMarkets } = require('../../kalshi/kalshiClient');
    const { getYesPrice }      = require('../../kalshi/kalshiAnalyzer');
    const markets = await getSportsMarkets();
    const priced  = markets.filter(m => getYesPrice(m) != null);

    const fmt = m => ({
      ticker:                   m.ticker,
      title:                    (m.title || '').slice(0, 80),
      yes_bid_dollars:          m.yes_bid_dollars,
      yes_ask_dollars:          m.yes_ask_dollars,
      last_price_dollars:       m.last_price_dollars,
      no_bid_dollars:           m.no_bid_dollars,
      no_ask_dollars:           m.no_ask_dollars,
      previous_yes_bid_dollars: m.previous_yes_bid_dollars,
      previous_yes_ask_dollars: m.previous_yes_ask_dollars,
      _computedYesPrice:        getYesPrice(m),
    });

    res.json({
      total:        markets.length,
      pricedCount:  priced.length,
      sampleAll:    markets.slice(0, 5).map(fmt),
      samplePriced: priced.slice(0, 10).map(fmt),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Delete a pick
router.delete('/picks/:id', requireAuth, (req, res) => {
  res.json({ ok: deletePick(req.params.id) });
});

// Mark a pick win or loss manually
router.patch('/picks/:id/result', requireAuth, (req, res) => {
  const { result } = req.body;
  if (!['win', 'loss'].includes(result)) return res.status(400).json({ error: 'Invalid result' });
  res.json({ ok: updatePickResult(req.params.id, result) });
});

// Kalshi portfolio — balance + open positions (requires credentials)
router.get('/api/portfolio', requireAuth, async (req, res) => {
  try {
    const { getBalance, getPositions } = require('../../kalshi/kalshiClient');
    const [balData, posData] = await Promise.all([getBalance(), getPositions()]);
    res.json({
      balance:   balData?.balance   ?? balData,
      positions: posData?.market_positions ?? posData?.positions ?? [],
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Full pick history page
router.get('/history', requireAuth, (req, res) => {
  const picks = loadPicks().slice().reverse();
  res.render('history', { user: req.session.user, picks });
});

// Generate parlays on demand from the dashboard
router.post('/generate', requireAuth, async (req, res) => {
  try {
    const tier   = ['safe', 'medium', 'high', 'all'].includes(req.body.tier) ? req.body.tier : 'all';
    const filter = typeof req.body.filter === 'string' && req.body.filter.trim()
      ? req.body.filter.trim().toLowerCase()
      : null;
    const useAll = req.body.useAll === 'true' || req.body.useAll === true;

    console.log('[/generate] Request:', { tier, filter, useAll });
    const data = await generateParlays({ tier, filter, useAll, save: true });
    console.log('[/generate] Result:', { results: data.results?.length, marketCount: data.marketCount });
    res.json(data);
  } catch (err) {
    console.error('[/generate] Error:', err.message, err.stack);
    res.status(500).json({ error: err.message || 'Failed to generate parlays' });
  }
});

// Live sports intel — upsets, live scores, hot markets
router.get('/upsets', requireAuth, async (req, res) => {
  try {
    const [gameMarkets, espnGames] = await Promise.all([
      getGameMarkets(),
      getLiveScores(),
    ]);

    const upsets     = findUpsetMarkets(gameMarkets);
    const hotMarkets = findHotMarkets(gameMarkets);

    // Attach matching Kalshi prices to each ESPN game (live + scheduled only)
    const liveGames = espnGames
      .filter(g => !g.isFinal)
      .map(game => {
        const matched = matchKalshiToGame(game, gameMarkets);
        const hOpts   = [game.home?.abbr, ESPN_ALT[game.home?.abbr]].filter(Boolean);
        const aOpts   = [game.away?.abbr, ESPN_ALT[game.away?.abbr]].filter(Boolean);
        const kalshi  = matched.map(m => {
          const side = m.ticker.split('-').pop();
          return {
            ticker:   m.ticker,
            side,
            isHome:   hOpts.includes(side),
            isAway:   aOpts.includes(side),
            yesPrice: getYesPrice(m),
          };
        });
        return { ...game, kalshi };
      });

    res.json({ upsets, liveGames, hotMarkets });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
