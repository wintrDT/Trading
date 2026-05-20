// utils/tradeLogger.js — logs web-UI executed trades and tracks settlement
const fs   = require('fs');
const path = require('path');
const { publicRequest } = require('../kalshi/kalshiClient');

const TRADES_FILE = path.join(__dirname, '../data/trades.json');

function loadTrades() {
  try {
    if (!fs.existsSync(TRADES_FILE)) return [];
    return JSON.parse(fs.readFileSync(TRADES_FILE, 'utf8'));
  } catch { return []; }
}

function saveTrades(trades) {
  const dir = path.dirname(TRADES_FILE);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(TRADES_FILE, JSON.stringify(trades, null, 2));
}

function logTrade({ ticker, title, side, type, count, fillPrice, orderId }) {
  const trades = loadTrades();
  const cost   = fillPrice != null ? +(fillPrice / 100 * count).toFixed(2) : null;
  trades.push({
    id:         orderId || String(Date.now()),
    ticker,
    title:      title || ticker,
    side,
    type,
    count,
    fillPrice,  // cents (yesPrice from order response, or null for market orders)
    cost,       // dollars
    orderId:    orderId || null,
    executedAt: new Date().toISOString(),
    result:     null,  // 'win' | 'loss' | null
    resolvedAt: null,
    payout:     null,  // dollars
  });
  saveTrades(trades);
}

// Check all pending trades against Kalshi settlement.
// Call this periodically (e.g. on page load, or via cron).
async function settleTrades() {
  const trades = loadTrades();
  let changed = false;

  for (const trade of trades) {
    if (trade.result !== null) continue;
    const ageMins = (Date.now() - new Date(trade.executedAt).getTime()) / 60000;
    if (ageMins < 30) continue; // skip very fresh trades

    try {
      const data   = await publicRequest('GET', `/markets/${trade.ticker}`, {}, 0);
      const market = data?.market ?? data;
      if ((market?.status === 'settled' || market?.status === 'closed') && market?.result) {
        trade.result     = market.result === trade.side ? 'win' : 'loss';
        trade.resolvedAt = new Date().toISOString();
        trade.payout     = trade.result === 'win' ? +trade.count.toFixed(2) : 0;
        changed = true;
      }
    } catch {}
  }

  if (changed) saveTrades(trades);
  return trades;
}

function getTradeStats() {
  const trades   = loadTrades();
  const resolved = trades.filter(t => t.result !== null);
  const wins     = resolved.filter(t => t.result === 'win').length;
  const losses   = resolved.filter(t => t.result === 'loss').length;
  const pending  = trades.filter(t => t.result === null).length;

  const netPL = resolved.reduce((sum, t) => {
    const cost   = t.cost   ?? 0;
    const payout = t.payout ?? 0;
    return sum + payout - cost;
  }, 0);

  const totalCost = resolved.reduce((s, t) => s + (t.cost ?? 0), 0);
  const roi = totalCost > 0 ? +((netPL / totalCost) * 100).toFixed(1) : null;

  return {
    total:   resolved.length,
    wins, losses, pending,
    winRate: resolved.length > 0 ? +((wins / resolved.length) * 100).toFixed(1) : null,
    netPL:   +netPL.toFixed(2),
    roi,
    recent:  trades.slice(-30).reverse(),
  };
}

module.exports = { logTrade, settleTrades, getTradeStats, loadTrades };
