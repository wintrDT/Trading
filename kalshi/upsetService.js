// kalshi/upsetService.js
// Detects live game markets where the price has shifted significantly from the
// previous quote, signaling an in-progress upset opportunity.
// Uses market fields already fetched — no extra API calls per market.

const { getYesPrice } = require('./kalshiAnalyzer');

const GAME_PREFIXES = ['KXNBAGAME', 'KXNFLGAME', 'KXMLBGAME', 'KXNHLGAME'];

function isGameMarket(m) {
  const t = (m.ticker || '').toUpperCase();
  return GAME_PREFIXES.some(p => t.startsWith(p));
}

// Keep markets closing within the next 36 hours (today + tomorrow)
function isRecentGame(m) {
  const tf = m.close_time ?? m.expiration_time ?? null;
  if (!tf) return true;
  const closeMs = new Date(tf).getTime();
  const now     = Date.now();
  return closeMs > now && closeMs < now + 36 * 60 * 60 * 1000;
}

// Dollar field → integer cents (1–99)
function toCents(v) {
  if (v == null || isNaN(v)) return null;
  const n = parseFloat(v);
  if (n > 1 && n < 100) return Math.round(n);   // already cents
  if (n > 0 && n <= 1)  return Math.round(n * 100); // dollars → cents
  return null;
}

function cleanTitle(m) {
  return (m.no_sub_title || m.title || m.ticker || '').replace(/^(yes|no):\s*/i, '').trim();
}

// Get the "previous" yes price from the market object's snapshot fields
function getPrevYesPrice(m) {
  const fields = [
    m.previous_yes_bid_dollars,  // Kalshi's own snapshot field (best)
    m.previous_yes_ask_dollars,
    m.last_price_dollars,        // last actual trade — compare to current yes_bid (Option A)
  ];
  for (const f of fields) {
    const c = toCents(f);
    if (c != null && c > 0 && c < 100) return c;
  }
  return null;
}

/**
 * Scans all game markets for price shifts between the current and previous quotes.
 * No extra API calls — uses data already on the market object.
 * minShift: minimum move in percentage points to flag as an upset.
 */
function findUpsetMarkets(markets, { minShift = 8, limit = 6 } = {}) {
  const candidates = markets.filter(m => isGameMarket(m));
  console.log(`[upsetService] ${markets.length} total markets, ${candidates.length} game markets`);
  if (candidates.length > 0) console.log('[upsetService] Sample game tickers:', candidates.slice(0, 5).map(m => m.ticker));

  const results = [];
  let skipNoPrice = 0, skipNoPrev = 0, skipSmallShift = 0;

  for (const m of candidates) {
    const yesNow  = getYesPrice(m);
    const yesPrev = getPrevYesPrice(m);

    if (yesNow == null)  { skipNoPrice++;  continue; }
    if (yesPrev == null) { skipNoPrev++;   continue; }

    const shift    = yesNow - yesPrev;
    const absShift = Math.abs(shift);
    if (absShift < minShift) { skipSmallShift++; continue; }

    // shift > 0: YES surging → bet YES (underdog coming back)
    // shift < 0: NO surging  → bet NO  (underdog pulling ahead)
    const upsetSide = shift > 0 ? 'yes' : 'no';
    const upsetProb = upsetSide === 'yes' ? yesNow  : 100 - yesNow;
    const openProb  = upsetSide === 'yes' ? yesPrev : 100 - yesPrev;
    const roi       = (((1 - upsetProb / 100) / (upsetProb / 100)) * 100).toFixed(1);

    results.push({
      ticker:    m.ticker,
      title:     cleanTitle(m),
      closeTime: m.close_time ?? m.expiration_time,
      yesNow,
      yesOpen:   yesPrev,
      shift,
      absShift,
      upsetSide,
      upsetProb,
      openProb,
      roi: parseFloat(roi),
    });
  }

  console.log(`[upsetService] Skipped: ${skipNoPrice} no-price, ${skipNoPrev} no-prev-price, ${skipSmallShift} small-shift (<${minShift}¢) of ${candidates.length} game markets`);
  console.log(`[upsetService] Found ${results.length} upset opportunities`);
  return results.sort((a, b) => b.absShift - a.absShift).slice(0, limit);
}

// Option C: surface game markets with the most trading activity
function findHotMarkets(markets, { limit = 6 } = {}) {
  return markets
    .filter(m => isGameMarket(m))
    .map(m => ({
      ticker:       m.ticker,
      title:        cleanTitle(m),
      yesPrice:     getYesPrice(m),
      volume:       m.volume       || 0,
      openInterest: m.open_interest || 0,
    }))
    .filter(m => m.volume > 0 || m.openInterest > 0)
    .sort((a, b) => (b.volume + b.openInterest) - (a.volume + a.openInterest))
    .slice(0, limit);
}

module.exports = { findUpsetMarkets, findHotMarkets, isGameMarket, isRecentGame };
