const router          = require('express').Router();
const { requireAuth } = require('../middleware');
const { getSportsMarkets, getMarkets } = require('../../kalshi/kalshiClient');
const { getYesPrice, bestSide, getSideProb } = require('../../kalshi/kalshiAnalyzer');

// Market browser page
router.get('/', requireAuth, async (req, res) => {
  try {
    const { q, sport, tier } = req.query;
    let markets = await getSportsMarkets();

    // Add price info to each market
    markets = markets.map(m => {
      const yp = getYesPrice(m);
      return {
        ...m,
        _yesPrice: yp,
        _side: yp != null ? bestSide(yp) : null,
        _sideProb: yp != null ? getSideProb(yp, bestSide(yp)) : null,
        _tier: yp != null ? riskTier(yp) : null,
      };
    });

    // Filter by search query
    if (q) {
      const query = q.toLowerCase();
      markets = markets.filter(m =>
        m.title?.toLowerCase().includes(query) ||
        m.ticker?.toLowerCase().includes(query) ||
        m.subtitle?.toLowerCase().includes(query)
      );
    }

    // Filter by sport (extracted from ticker prefix)
    if (sport) {
      const prefixMap = {
        nba: 'KXNBA',
        nfl: 'KXNFL',
        mlb: 'KXMLB',
        nhl: 'KXNHL',
      };
      const prefix = prefixMap[sport.toLowerCase()];
      if (prefix) {
        markets = markets.filter(m => m.ticker?.startsWith(prefix));
      }
    }

    // Filter by risk tier
    if (tier && ['safe', 'medium', 'high'].includes(tier)) {
      markets = markets.filter(m => m._tier === tier);
    }

    res.render('markets', {
      user: req.session.user,
      markets: markets.slice(0, 100), // Limit display
      totalCount: markets.length,
      query: q || '',
      selectedSport: sport || '',
      selectedTier: tier || '',
    });
  } catch (err) {
    console.error('[markets]', err.message);
    res.render('error', { message: 'Failed to load markets: ' + err.message });
  }
});

// API endpoint for market search
router.get('/api/search', requireAuth, async (req, res) => {
  try {
    const { q, limit = 20 } = req.query;
    if (!q) return res.json({ results: [] });

    const markets = await getSportsMarkets();
    const query = q.toLowerCase();
    const results = markets
      .filter(m =>
        m.title?.toLowerCase().includes(query) ||
        m.ticker?.toLowerCase().includes(query)
      )
      .slice(0, parseInt(limit));

    res.json({ results });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

function riskTier(yesPrice) {
  const prob = yesPrice >= 50 ? yesPrice : 100 - yesPrice;
  if (prob >= 70) return 'safe';
  if (prob >= 50) return 'medium';
  return 'high';
}

module.exports = router;
