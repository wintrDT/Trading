// kalshi/kalshiAnalyzer.js

const { getMarketHistory } = require('./kalshiClient');
const { normalizeName }    = require('../utils/propsApi');

// ── Vegas edge scoring ─────────────────────────────────────────────────────────

// Maps words found in Kalshi titles to Odds API market keys
const STAT_TO_MARKET = {
  'points': 'player_points',   'pts': 'player_points',
  'rebounds': 'player_rebounds','rebs': 'player_rebounds', 'reb': 'player_rebounds',
  'assists': 'player_assists',  'ast': 'player_assists',
  'threes': 'player_threes',   '3pm': 'player_threes',   '3-pointers': 'player_threes',
  'steals': 'player_steals',   'stl': 'player_steals',
  'blocks': 'player_blocks',   'blk': 'player_blocks',
  'hits': 'batter_hits',
  'home runs': 'batter_home_runs', 'hr': 'batter_home_runs',
  'rbis': 'batter_rbis',       'rbi': 'batter_rbis',
  'strikeouts': 'pitcher_strikeouts', 'so': 'pitcher_strikeouts',
};

// Parse "LeBron James: 25+ points" → { player, threshold, statMarket }
function parseKalshiLeg(text) {
  const m = text.match(/^(.+?):\s*(\d+)\+\s*(.+)$/);
  if (!m) return null;
  const [, name, thresholdStr, statRaw] = m;
  const statMarket = STAT_TO_MARKET[statRaw.toLowerCase().trim()];
  if (!statMarket) return null;
  return { player: normalizeName(name), threshold: parseInt(thresholdStr), statMarket };
}

// Find the Vegas line closest to a Kalshi threshold (within 1.5 units)
function findVegasProp(propMap, player, statMarket, threshold) {
  let best = null;
  let bestDist = Infinity;
  for (const [key, val] of Object.entries(propMap)) {
    const [mapPlayer, mapStat, lineStr] = key.split('|');
    if (mapStat !== statMarket) continue;
    // Flexible name match: either contains the other
    if (!mapPlayer.includes(player) && !player.includes(mapPlayer)) continue;
    const dist = Math.abs(parseFloat(lineStr) - threshold);
    if (dist <= 1.5 && dist < bestDist) { best = val; bestDist = dist; }
  }
  return best;
}

/**
 * Annotates each Kalshi market with _vegasProb and _edge (in percentage points).
 * Edge > 0 means Kalshi is underpricing our side relative to Vegas — a value bet.
 * Markets with no Vegas match are left unchanged.
 */
function attachVegasEdge(markets, propMap) {
  if (!propMap || !Object.keys(propMap).length) return markets;

  return markets.map(m => {
    const title = m.no_sub_title || m.title || '';
    const legs  = title.split(',').map(s => s.trim());

    const edges = [];
    for (const leg of legs) {
      const parsed = parseKalshiLeg(leg);
      if (!parsed) continue;
      const prop = findVegasProp(propMap, parsed.player, parsed.statMarket, parsed.threshold);
      if (!prop) continue;
      const kalshiProb = m._sideProb / 100;
      const vegasProb  = m._side === 'yes' ? prop.overProb : prop.underProb;
      edges.push({ edge: vegasProb - kalshiProb, vegasProb });
    }

    if (!edges.length) return m;
    const best = edges.sort((a, b) => b.edge - a.edge)[0];
    return {
      ...m,
      _vegasProb: Math.round(best.vegasProb * 100),
      _edge:      Math.round(best.edge * 100), // percentage points, positive = value
    };
  });
}

/**
 * Get yes price in cents (1–99).
 * Kalshi v2 API returns only dollar-denominated fields (0.01–0.99).
 * Falls back through: live YES → live NO inverted → previous YES → previous NO inverted.
 */
function getYesPrice(market) {
  // Direct YES-side dollar fields
  const yesFields = [
    'yes_bid_dollars', 'yes_ask_dollars', 'last_price_dollars',
    'previous_yes_bid_dollars', 'previous_yes_ask_dollars',
  ];
  for (const f of yesFields) {
    const v = parseFloat(market[f] ?? 0);
    if (!isNaN(v) && v > 0 && v < 1) return Math.round(v * 100);
  }

  // NO-side dollar fields — invert to get YES price (yes ≈ 1 - no)
  // no_ask = what sellers want for NO ≈ 1 - yes_bid
  // no_bid = best NO bid ≈ 1 - yes_ask
  const noInvertFields = ['no_ask_dollars', 'no_bid_dollars'];
  for (const f of noInvertFields) {
    const v = parseFloat(market[f] ?? 0);
    if (!isNaN(v) && v > 0 && v < 1) return Math.round((1 - v) * 100);
  }

  // previous_prices_dollars is an array — grab most recent entry
  if (Array.isArray(market.previous_prices_dollars) && market.previous_prices_dollars.length > 0) {
    const last = market.previous_prices_dollars[market.previous_prices_dollars.length - 1];
    const v = parseFloat(last?.yes ?? last?.yes_price ?? last?.price ?? 0);
    if (!isNaN(v) && v > 0 && v < 1) return Math.round(v * 100);
  }

  // Legacy integer cent fields (older API versions)
  for (const f of ['yes_bid', 'yes_ask', 'last_price', 'previous_yes_bid', 'previous_yes_ask']) {
    const v = parseInt(market[f] ?? 0, 10);
    if (v > 0 && v < 100) return v;
  }
  return null;
}

/**
 * Risk tiers based on the BETTER side's probability.
 * We always take the more likely side, so:
 *   safe   = likely side is 70-90%
 *   medium = likely side is 50-70%
 *   high   = YES at 20-50% (betting the underdog for big payout potential)
 */
function riskTier(yesPrice) {
  const prob = yesPrice >= 50 ? yesPrice : 100 - yesPrice;
  if (prob >= 70) return 'safe';    // 70-90%: heavy favorites
  if (prob >= 50) return 'medium';  // 50-70%: moderate edge
  return 'high';                    // underdog territory
}

/**
 * Best side to take = whichever side has >50% implied probability.
 * For a 30¢ YES market, take NO (70% implied).
 * For a 72¢ YES market, take YES (72% implied).
 */
function bestSide(yesPrice) {
  return yesPrice >= 50 ? 'yes' : 'no';
}

function getSideProb(yesPrice, side) {
  return side === 'yes' ? yesPrice : 100 - yesPrice;
}

function scoreMarket(market) {
  const yesPrice = getYesPrice(market);
  if (yesPrice == null) return null;

  const volume     = parseFloat(market.volume_24h_fp ?? market.volume_fp ?? 0);
  const openInt    = parseFloat(market.open_interest_fp ?? 0);
  const side       = bestSide(yesPrice);
  const sideProb   = getSideProb(yesPrice, side);
  const conviction = Math.abs(yesPrice - 50); // 0 = pure 50/50, 50 = certain

  return {
    total: conviction,  // higher = more certain = safer
    liquidity: Math.min((volume + openInt) * 10, 100),
    conviction,
    sideProb,
    yesPrice,
    volume,
  };
}

// ── Real-world data adjustments ──────────────────────────────────────────────
const { avgFieldFromTicker } = require('../utils/sportsData');

/**
 * Adjusts _sideProb for each market based on injuries, season/recent averages,
 * team records, back-to-back flags, and minutes (low-minute players filtered out).
 * Returns the filtered, adjusted array.
 */
function applyRealWorldFilter(markets, ctx) {
  if (!ctx) return markets;
  const { injuries, teamRecords, playerAvgs, recentAvgs, b2bTeams } = ctx;

  return markets.map(m => {
    const ticker   = (m.ticker || '').toUpperCase();
    const avgField = avgFieldFromTicker(ticker);

    // ── Individual player prop ──────────────────────────────────────────────
    if (avgField) {
      const raw   = m.no_sub_title || m.title || '';
      const match = raw.match(/^([^:,0-9][^:]{2,}):\s*([\d.]+)\+/);
      if (!match) return m;

      const playerName = match[1].trim();
      const threshold  = parseFloat(match[2]);
      const norm       = normalizeName(playerName);

      // Injury gate — skip Out/Doubtful
      const injuryStatus = injuries?.get(norm);
      if (injuryStatus === 'out' || injuryStatus === 'doubtful') {
        console.log(`[realWorld] Skip ${playerName} — ${injuryStatus}`);
        return null;
      }

      const seasonAvg = playerAvgs?.get(norm);
      const recent    = recentAvgs?.get(norm);

      // Minutes gate: skip bench players unlikely to accumulate stats
      if (seasonAvg?.minutes != null && seasonAvg.minutes < 15) {
        console.log(`[realWorld] Skip ${playerName} — low minutes (${seasonAvg.minutes})`);
        return null;
      }

      let adj = { ...m };

      // Tag team for correlation grouping
      if (seasonAvg?.team) adj._team = seasonAvg.team;

      // Back-to-back penalty
      if (b2bTeams?.size && seasonAvg?.team && b2bTeams.has(seasonAvg.team)) {
        adj._sideProb    = Math.max(15, adj._sideProb - 12);
        adj._realWorldNote = 'B2B rest risk';
        console.log(`[realWorld] B2B penalty: ${playerName} (${seasonAvg.team})`);
      }

      // Injury status penalty
      if (injuryStatus === 'questionable') {
        adj._sideProb      = Math.max(15, adj._sideProb - 15);
        adj._realWorldNote = 'questionable';
      } else if (injuryStatus === 'dtd') {
        adj._sideProb      = Math.max(15, adj._sideProb - 8);
        adj._realWorldNote = 'day-to-day';
      }

      // Prefer recent form (L5) over season avg — more predictive
      const src      = recent ?? seasonAvg;
      const isRecent = recent != null;
      const statAvg  = src?.[avgField];
      const label    = isRecent ? `L5 avg ${statAvg}` : `avg ${statAvg}`;

      if (statAvg != null && threshold > 0) {
        const ratio = statAvg / threshold;
        if (ratio >= 1.5) {
          adj._sideProb      = Math.min(94, adj._sideProb + 12);
          adj._realWorldNote = `${label} >> ${threshold} (hot)`;
        } else if (ratio >= 1.25) {
          adj._sideProb      = Math.min(94, adj._sideProb + 8);
          adj._realWorldNote = `${label} > ${threshold}`;
        } else if (ratio >= 1.05) {
          adj._sideProb      = Math.min(94, adj._sideProb + 4);
          adj._realWorldNote = `${label} ≈ ${threshold}`;
        } else if (ratio < 0.70) {
          console.log(`[realWorld] Skip ${playerName} — ${label} << ${threshold}`);
          return null;
        } else if (ratio < 0.85) {
          adj._sideProb      = Math.max(15, adj._sideProb - 12);
          adj._realWorldNote = `${label} < ${threshold} (cold)`;
        }
      }

      // Attach all available player averages so the UI can show a full stat line
      if (src) {
        const statsLabel = isRecent ? 'L5' : 'Avg';
        const parts = [];
        if (src.pts  != null && src.pts  > 0) parts.push(`${src.pts}pts`);
        if (src.reb  != null && src.reb  > 0) parts.push(`${src.reb}reb`);
        if (src.ast  != null && src.ast  > 0) parts.push(`${src.ast}ast`);
        if (src.fg3m != null && src.fg3m > 0) parts.push(`${src.fg3m}3PM`);
        if (src.stl  != null && src.stl  > 0.3) parts.push(`${src.stl}stl`);
        if (src.blk  != null && src.blk  > 0.3) parts.push(`${src.blk}blk`);
        if (parts.length > 1) adj._playerStatLine = `${statsLabel}: ${parts.join(' · ')}`;
      }

      return adj;
    }

    // ── Game/team market — factor in team win% + B2B ────────────────────────
    if (teamRecords?.size) {
      const yesSide = m.ticker?.split('-').pop()?.toUpperCase();
      const record  = yesSide ? teamRecords.get(yesSide) : null;
      if (record) {
        const wp = record.winPct;
        if (wp >= 0.65) {
          m = { ...m, _sideProb: Math.min(94, m._sideProb + 6), _realWorldNote: `${yesSide} ${Math.round(wp*100)}% WR` };
        } else if (wp <= 0.35) {
          m = { ...m, _sideProb: Math.max(15, m._sideProb - 8), _realWorldNote: `${yesSide} weak ${Math.round(wp*100)}%` };
        }
      }
      if (b2bTeams?.size && yesSide && b2bTeams.has(yesSide)) {
        m = { ...m, _sideProb: Math.max(15, m._sideProb - 8),
              _realWorldNote: (m._realWorldNote ? m._realWorldNote + ', ' : '') + 'B2B' };
      }
    }

    return m;
  }).filter(Boolean);
}

// Individual player stat markets — can only bet YES on Kalshi (no NO side available).
// Game lines and totals allow either side.
const INDIVIDUAL_PROP_PREFIXES = [
  'KXNBAPTS','KXNBAAST','KXNBAREB','KXNBATHRPM','KXNBASTL','KXNBABLK',
  'KXMLBHITS','KXMLBHR','KXMLBRBI','KXMLBSO',
  'KXNFLPASS','KXNFLRUSH','KXNFLREC','KXNFLTD',
];

function isIndividualProp(market) {
  const ticker = (market.ticker || '').toUpperCase();
  return INDIVIDUAL_PROP_PREFIXES.some(p => ticker.startsWith(p));
}

/**
 * Build a parlay.
 *
 * Tier logic for individual player props (YES only):
 *   safe   — YES at 70–90%  (e.g. "Fox 10+ points", near-certain)
 *   medium — YES at 50–70%  (e.g. "Tatum 25+ points")
 *   high   — YES at 20–50%  (e.g. "Fox 40+ points", longshot)
 *
 * For game lines / MVE bundles: take the better side (YES or NO).
 */
function buildKalshiParlay(markets, tier = 'safe', numLegs = 3, propMap = null, sportsContext = null) {
  // Pre-filter: keep individual player prop markets AND MVE bundles with prop patterns
  const cleanMarkets = markets.filter(m => {
    const title  = (m.no_sub_title || m.title || '');
    const ticker = (m.ticker || '').toUpperCase();

    if (INDIVIDUAL_PROP_PREFIXES.some(p => ticker.startsWith(p))) return true;

    const hasPlayerProp = /[a-zA-Z]{3,}:\s*\d+\+/i.test(title);
    if (!hasPlayerProp) return false;
    const totalMatches = (title.match(/[Tt]otal\s+\d+/g) || []).length;
    if (totalMatches >= 2) return false;
    if (/\d{2}[a-zA-Z]{3}\d{2}/.test(title)) return false;
    if (/NO:\s*[A-Z]{2,4}\d/i.test(title)) return false;
    return true;
  });
  console.log("[buildKalshiParlay] Pre-filter:", cleanMarkets.length, "of", markets.length);

  const baseScored = cleanMarkets
    .map(m => {
      const yp = getYesPrice(m);
      if (yp == null) return null;

      // Always bet YES — only include markets where YES side meets the probability bar
      const side     = 'yes';
      const sideProb = yp;
      let t;
      if (isIndividualProp(m)) {
        t = yp >= 70 ? 'safe' : yp >= 50 ? 'medium' : 'high';
      } else {
        t = riskTier(yp);
      }

      const cost = sideProb / 100;
      const roi  = (1 - cost) / cost;
      return { ...m, _yesPrice: yp, _side: side, _sideProb: sideProb, _tier: t, _roi: roi };
    })
    .filter(Boolean);

  // Attach Vegas edge scores if propMap is available
  const vegasScored = attachVegasEdge(baseScored, propMap);

  // Apply real-world filter: remove injured players, adjust probabilities based on
  // season averages and team records
  const scored = applyRealWorldFilter(vegasScored, sportsContext);
  console.log(`[buildKalshiParlay] After real-world filter: ${scored.length} markets`);

  // Composite score: probability + real-world confidence + Vegas edge bonus
  // Edge > 8 = Kalshi is meaningfully mispriced vs consensus → strong signal
  const compositeScore = m => {
    let score = m._sideProb;
    const edge = m._edge ?? 0;
    if (edge >= 15) score += 15;        // huge mispricing
    else if (edge >= 8) score += 8;     // solid value
    else if (edge >= 3) score += 3;     // slight edge
    else if (edge < -5) score -= 6;     // Kalshi overpriced vs Vegas
    return score;
  };

  const byComposite    = (a, b) => compositeScore(b) - compositeScore(a);
  const byRoiComposite = (a, b) => {
    const scoreDiff = compositeScore(b) - compositeScore(a);
    if (Math.abs(scoreDiff) >= 8) return scoreDiff;
    return b._roi - a._roi;
  };

  console.log(`[buildKalshiParlay] ${markets.length} in → ${scored.length} priced, tier="${tier}"`);

  let pool;
  if (tier === 'safe') {
    pool = scored
      .filter(m => m._sideProb >= 70 && m._sideProb <= 95)
      .sort(byComposite);
    if (pool.length < numLegs) {
      const extra = scored.filter(m => m._sideProb >= 65 && m._sideProb < 70).sort(byComposite);
      pool = [...pool, ...extra];
    }
  } else if (tier === 'medium') {
    pool = scored
      .filter(m => m._sideProb >= 55 && m._sideProb < 70)
      .sort(byComposite);
    if (pool.length < numLegs) {
      const extra = scored.filter(m => m._sideProb >= 50 && m._sideProb < 55).sort(byComposite);
      pool = [...pool, ...extra];
    }
  } else {
    pool = scored
      .filter(m => m._sideProb >= 20 && m._sideProb <= 50)
      .sort(byRoiComposite);
    if (pool.length < numLegs) {
      const extra = scored.filter(m => m._sideProb > 50 && m._sideProb < 56).sort(byRoiComposite);
      pool = [...pool, ...extra];
    }
  }

  console.log(`[buildKalshiParlay] Pool size for ${tier}: ${pool.length}`);

  const titleSimilar = (a, b) => {
    const shorter = Math.min(a.length, b.length);
    let matches = 0;
    for (let i = 0; i < shorter; i++) if (a[i] === b[i]) matches++;
    return matches / shorter > 0.75;
  };

  // ── Same-game correlation: prefer leg clusters from the same matchup ──────
  // Count how many pool legs belong to each team; favour the team with the most
  // high-quality props so they're positively correlated in a single game.
  const teamCount = new Map();
  for (const m of pool.slice(0, 20)) {
    if (m._team) teamCount.set(m._team, (teamCount.get(m._team) || 0) + 1);
  }
  const dominantTeam = [...teamCount.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  const correlationAvailable = dominantTeam && (teamCount.get(dominantTeam) || 0) >= 2;

  const selected   = [];
  const usedTitles = [];

  // First pass: fill up to numLegs-1 slots with legs from the dominant game
  if (correlationAvailable) {
    for (const m of pool) {
      if (selected.length >= numLegs - 1) break;
      if (m._team !== dominantTeam) continue;
      const title = (m.no_sub_title || m.title || '').toLowerCase().slice(0, 40);
      if (usedTitles.some(t => titleSimilar(t, title))) continue;
      selected.push(m);
      usedTitles.push(title);
    }
  }

  // Fill remaining slots from the rest of the pool (any team)
  for (const m of pool) {
    if (selected.length >= numLegs) break;
    const title = (m.no_sub_title || m.title || '').toLowerCase().slice(0, 40);
    if (usedTitles.some(t => titleSimilar(t, title))) continue;
    selected.push(m);
    usedTitles.push(title);
  }
  if (selected.length < 2) return null;

  const combinedProb = selected.reduce((acc, m) => acc * (m._sideProb / 100), 1);
  const totalCost    = selected.reduce((acc, m) => acc + m._sideProb / 100, 0);
  const totalPayout  = selected.length;
  const profit       = totalPayout - totalCost;
  const roi          = ((profit / totalCost) * 100).toFixed(1);

  return { legs: selected, combinedProb, totalCost: totalCost.toFixed(2), totalPayout, profit: profit.toFixed(2), roi, tier };
}

function findValueMarkets(markets, limit = 5) {
  return markets
    .map(m => {
      const yp = getYesPrice(m);
      if (!yp) return null;
      const side = bestSide(yp);
      const sp   = getSideProb(yp, side);
      const vol  = parseFloat(m.volume_24h_fp ?? 0);
      // Value = uncertain markets (near 50) with some volume
      const valueScore = (50 - Math.abs(sp - 50)) + Math.min(vol * 5, 30);
      return { ...m, valueScore, _yesPrice: yp, _sideProb: sp, _side: side };
    })
    .filter(Boolean)
    .sort((a, b) => b.valueScore - a.valueScore)
    .slice(0, limit);
}

async function findMomentumMarkets(markets, limit = 5) {
  const results = [];
  for (const m of markets.slice(0, 20)) {
    try {
      const history = await getMarketHistory(m.ticker, 60);
      const candles = history?.history || [];
      if (candles.length < 3) continue;
      const movement = (candles[candles.length - 1]?.yes_price ?? 0) - (candles[0]?.yes_price ?? 0);
      if (Math.abs(movement) >= 5) results.push({ ...m, priceMovement: movement });
    } catch {}
  }
  return results.sort((a, b) => Math.abs(b.priceMovement) - Math.abs(a.priceMovement)).slice(0, limit);
}

module.exports = {
  scoreMarket, riskTier, bestSide, getSideProb,
  buildKalshiParlay, attachVegasEdge, applyRealWorldFilter,
  findValueMarkets, findMomentumMarkets, getYesPrice,
};
