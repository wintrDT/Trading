const fs   = require('fs');
const path = require('path');
const { publicRequest } = require('../kalshi/kalshiClient');

const DATA_FILE = path.join(__dirname, '../data/picks.json');

function loadPicks() {
  try {
    if (!fs.existsSync(DATA_FILE)) return [];
    return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  } catch { return []; }
}

function savePicks(picks) {
  const dir = path.dirname(DATA_FILE);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(DATA_FILE, JSON.stringify(picks, null, 2));
}

function savePick(parlay, tierLabel, date) {
  const picks = loadPicks();
  const id    = `${Date.now()}`;
  picks.push({
    id,
    postedAt:    new Date().toISOString(),
    tier:        tierLabel,
    date,
    legs: parlay.legs.map(m => ({
      ticker:   m.ticker,
      title:    m._enrichedTitle || m.no_sub_title || m.title || m.ticker,
      side:     m._side,
      sideProb: m._sideProb,
      result:   null,
    })),
    combinedProb: parlay.combinedProb,
    totalCost:    parlay.totalCost,
    totalPayout:  parlay.totalPayout,
    roi:          parlay.roi,
    result:       null,
    resolvedAt:   null,
  });
  savePicks(picks);
  return id;
}

// Checks all pending picks against the Kalshi API.
// Returns any picks that newly resolved during this check.
async function checkPendingPicks() {
  const picks    = loadPicks();
  const resolved = [];
  let   changed  = false;

  for (const pick of picks) {
    if (pick.result !== null) continue;

    // Don't check picks less than 2 hours old — games haven't finished yet
    const ageHours = (Date.now() - new Date(pick.postedAt).getTime()) / (1000 * 60 * 60);
    if (ageHours < 2) continue;

    let anyLoss    = false;
    let allSettled = true;

    for (const leg of pick.legs) {
      if (leg.result !== null) {
        if (leg.result === 'loss') anyLoss = true;
        continue;
      }
      try {
        const data   = await publicRequest('GET', `/markets/${leg.ticker}`, {}, 0);
        const market = data?.market ?? data;
        const status = market?.status;
        const mResult = market?.result; // 'yes' or 'no'

        if ((status === 'settled' || status === 'closed') && mResult) {
          leg.result = mResult === leg.side ? 'win' : 'loss';
          if (leg.result === 'loss') anyLoss = true;
          changed = true;
        } else {
          allSettled = false;
        }
      } catch { allSettled = false; }
    }

    // A parlay loses as soon as any leg loses, even if others are still open
    if (anyLoss) {
      pick.result     = 'loss';
      pick.resolvedAt = new Date().toISOString();
      resolved.push(pick);
      changed = true;
    } else if (allSettled) {
      pick.result     = 'win';
      pick.resolvedAt = new Date().toISOString();
      resolved.push(pick);
      changed = true;
    }
  }

  if (changed) savePicks(picks);
  return resolved;
}

function getStats() {
  const picks    = loadPicks();
  const resolved = picks.filter(p => p.result !== null);
  const byTier   = {};

  for (const pick of resolved) {
    if (!byTier[pick.tier]) byTier[pick.tier] = { wins: 0, losses: 0, profit: 0 };
    const t = byTier[pick.tier];
    if (pick.result === 'win') {
      t.wins++;
      t.profit += parseFloat(pick.totalPayout) - parseFloat(pick.totalCost);
    } else {
      t.losses++;
      t.profit -= parseFloat(pick.totalCost);
    }
  }

  const wins   = resolved.filter(p => p.result === 'win').length;
  const losses = resolved.filter(p => p.result === 'loss').length;
  const profit = resolved.reduce((sum, p) => {
    return p.result === 'win'
      ? sum + parseFloat(p.totalPayout) - parseFloat(p.totalCost)
      : sum - parseFloat(p.totalCost);
  }, 0);

  return {
    total:   resolved.length,
    wins,
    losses,
    profit,
    pending: picks.filter(p => p.result === null).length,
    byTier,
    recent:  picks.slice(-10).reverse(),
  };
}

// Save a parlay executed from the spreads page
function saveExecutedParlay(filledLegs, wager, maxPayout, date) {
  const picks = loadPicks();
  const id    = String(Date.now());
  const roi   = wager > 0 ? +(((maxPayout - wager) / wager) * 100).toFixed(1) : 0;
  picks.push({
    id,
    postedAt:    new Date().toISOString(),
    tier:        'Executed',
    source:      'spreads',
    date,
    legs: filledLegs.map(r => ({
      ticker:    r.ticker,
      title:     r.title || r.ticker,
      side:      r.side,
      sideProb:  null,
      count:     r.count,
      fillPrice: r.fillPrice ?? null,
      result:    null,
    })),
    wager:       +wager.toFixed(2),
    totalCost:   +wager.toFixed(2),
    totalPayout: +maxPayout.toFixed(2),
    roi,
    result:      null,
    resolvedAt:  null,
  });
  savePicks(picks);
  return id;
}

function deletePick(id) {
  const picks = loadPicks();
  const next  = picks.filter(p => p.id !== id);
  if (next.length === picks.length) return false;
  savePicks(next);
  return true;
}

function updatePickResult(id, result) {
  const picks = loadPicks();
  const pick  = picks.find(p => p.id === id);
  if (!pick) return false;
  pick.result     = result;
  pick.resolvedAt = new Date().toISOString();
  savePicks(picks);
  return true;
}

module.exports = { savePick, saveExecutedParlay, deletePick, updatePickResult, checkPendingPicks, getStats, loadPicks };
