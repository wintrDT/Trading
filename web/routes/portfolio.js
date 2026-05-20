// web/routes/portfolio.js
const router = require('express').Router();
const { requireAuth } = require('../middleware');
const { getMarket } = require('../../kalshi/kalshiClient');
const { getClient } = require('../../kalshi/userClient');
const db = require('../db');
const { getTradeStats, settleTrades } = require('../../utils/tradeLogger');

router.get('/', requireAuth, async (req, res) => {
  const client     = getClient(req.session.user.id, db);
  const tradeStats = getTradeStats();

  if (!client.hasCredentials) {
    return res.render('portfolio', {
      user: req.session.user,
      balance: null, positions: [], orders: [], tradeStats,
      error: 'No Kalshi credentials set. Go to Settings → Kalshi Account to add them.',
    });
  }

  try {
    const [balData, posData, ordData] = await Promise.all([
      client.getBalance().catch(() => null),
      client.getPositions({ limit: 100 }).catch(() => ({ positions: [] })),
      client.getOrders({ status: 'resting', limit: 100 }).catch(() => ({ orders: [] })),
    ]);

    const rawBalance   = balData?.balance ?? null;
    const rawPositions = posData?.positions || [];
    const rawOrders    = ordData?.orders    || [];

    const orders = rawOrders.map(o => ({
      orderId:     o.order_id,
      ticker:      o.ticker,
      action:      o.action || 'buy',
      side:        o.side,
      type:        o.type,
      yesPrice:    o.yes_price,
      noPrice:     o.no_price,
      count:       o.count,
      filledCount: o.filled_count || 0,
      remaining:   o.remaining_count ?? o.count,
      status:      o.status,
      createdTime: o.created_time,
    }));

    // Enrich positions with live market prices (uses public API — no auth needed)
    const positions = await Promise.all(
      rawPositions.map(async (p) => {
        const side = p.position > 0 ? 'yes' : 'no';
        const qty  = Math.abs(p.position);
        const base = {
          ticker: p.ticker, title: p.ticker, side, qty,
          marketExposure: p.market_exposure != null ? +(p.market_exposure / 100).toFixed(2) : null,
        };
        try {
          const mktData = await getMarket(p.ticker);
          const m = mktData?.market ?? mktData;
          const cents = v => v != null && !isNaN(v) ? Math.round(parseFloat(v) <= 1 ? parseFloat(v) * 100 : parseFloat(v)) : null;
          const yesBid = cents(m.yes_bid_dollars);
          const yesAsk = cents(m.yes_ask_dollars);
          const last   = cents(m.last_price_dollars);
          const bidPrice = side === 'yes'
            ? (yesBid ?? last)
            : (yesAsk != null ? 100 - yesAsk : (last != null ? 100 - last : null));
          const mktValue = bidPrice != null ? +(qty * bidPrice / 100).toFixed(2) : null;
          return { ...base, title: m.yes_sub_title || m.no_sub_title || m.title || p.ticker, yesBid, yesAsk, last, bidPrice, mktValue, closeTime: m.close_time || null };
        } catch { return base; }
      })
    );

    const balance = rawBalance != null ? +(rawBalance / 100).toFixed(2) : null;

    // Settle pending trades async — don't block page load
    settleTrades().catch(() => {});

    res.render('portfolio', { user: req.session.user, balance, positions, orders, tradeStats, error: null });
  } catch (e) {
    console.error('[portfolio]', e.message);
    if (e.response?.status === 401 || e.response?.status === 403) client.invalidateSession();
    res.render('portfolio', {
      user: req.session.user, balance: null, positions: [], orders: [], tradeStats,
      error: e.response?.data?.message || e.message,
    });
  }
});

router.post('/cancel', requireAuth, async (req, res) => {
  try {
    const { orderId } = req.body;
    if (!orderId) return res.status(400).json({ ok: false, error: 'orderId required' });
    const client = getClient(req.session.user.id, db);
    if (!client.hasCredentials) return res.status(403).json({ ok: false, error: 'Kalshi credentials not set.' });
    await client.cancelOrder(orderId);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.response?.data?.message || e.message });
  }
});

module.exports = router;
