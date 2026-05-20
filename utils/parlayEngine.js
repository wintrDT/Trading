// utils/parlayEngine.js
const { EmbedBuilder } = require('discord.js');
const { fetchAllOdds } = require('./oddsApi');

function impliedProb(americanOdds) {
  if (americanOdds > 0) return 100 / (americanOdds + 100);
  return Math.abs(americanOdds) / (Math.abs(americanOdds) + 100);
}

function toDecimal(americanOdds) {
  if (americanOdds > 0) return americanOdds / 100 + 1;
  return 100 / Math.abs(americanOdds) + 1;
}

function bestOdds(bookmakers, marketKey, outcomeName) {
  let best = null;
  for (const bk of bookmakers) {
    const market = bk.markets?.find(m => m.key === marketKey);
    if (!market) continue;
    const outcome = market.outcomes?.find(o => o.name === outcomeName);
    if (!outcome) continue;
    if (best === null || outcome.price > best) best = outcome.price;
  }
  return best;
}

function riskLabel(prob) {
  if (prob >= 0.62) return 'safe';
  if (prob >= 0.48) return 'medium';
  return 'high';
}

function fmtOdds(o) {
  return o > 0 ? `+${o}` : `${o}`;
}

function extractLegs(games) {
  const legs = [];

  for (const game of games) {
    const { id, home_team, away_team, bookmakers, sportMeta, commence_time } = game;
    if (!bookmakers?.length) continue;

    const gameTime = new Date(commence_time);
    const now = new Date();
    if (gameTime < now || gameTime - now > 72 * 60 * 60 * 1000) continue;

    const gameKey = id || `${home_team}__${away_team}`;

    // Moneylines
    for (const team of [home_team, away_team]) {
      const odds = bestOdds(bookmakers, 'h2h', team);
      if (odds == null) continue;
      const prob = impliedProb(odds);
      legs.push({
        type: 'Moneyline', gameKey,
        pick: team, odds, prob, risk: riskLabel(prob),
        sportName: sportMeta.name, sportEmoji: sportMeta.emoji,
        label: `${sportMeta.emoji} **${team}** ML`,
      });
    }

    // Spreads - best odds per team across all bookmakers
    const spreadMap = {};
    for (const bk of bookmakers) {
      const market = bk.markets?.find(m => m.key === 'spreads');
      if (!market) continue;
      for (const o of market.outcomes) {
        if (!spreadMap[o.name] || o.price > spreadMap[o.name].price) {
          spreadMap[o.name] = { point: o.point, price: o.price };
        }
      }
    }
    for (const [teamName, { point, price }] of Object.entries(spreadMap)) {
      const prob = impliedProb(price);
      const ptStr = point > 0 ? `+${point}` : `${point}`;
      legs.push({
        type: 'Spread', gameKey,
        pick: teamName, point, odds: price, prob, risk: riskLabel(prob),
        sportName: sportMeta.name, sportEmoji: sportMeta.emoji,
        label: `${sportMeta.emoji} **${teamName} ${ptStr}** (spread)`,
      });
    }

    // Totals - best odds per side across all bookmakers
    const totalMap = {};
    for (const bk of bookmakers) {
      const market = bk.markets?.find(m => m.key === 'totals');
      if (!market) continue;
      for (const o of market.outcomes) {
        if (!totalMap[o.name] || o.price > totalMap[o.name].price) {
          totalMap[o.name] = { point: o.point, price: o.price };
        }
      }
    }
    for (const [side, { point, price }] of Object.entries(totalMap)) {
      const prob = impliedProb(price);
      legs.push({
        type: 'Total', gameKey,
        pick: `${side} ${point}`, odds: price, prob, risk: riskLabel(prob),
        sportName: sportMeta.name, sportEmoji: sportMeta.emoji,
        label: `${sportMeta.emoji} **${side} ${point}** (${away_team} @ ${home_team})`,
      });
    }
  }

  const seen = new Set();
  return legs.filter(l => {
    if (seen.has(l.label)) return false;
    seen.add(l.label);
    return true;
  });
}

const TYPE_TARGETS = {
  safe:   { Moneyline: 1, Spread: 1, Total: 1 },
  medium: { Moneyline: 2, Spread: 1, Total: 1 },
  high:   { Moneyline: 2, Spread: 2, Total: 1 },
};

function buildParlay(legs, riskTier, numLegs = 4) {
  const targets = TYPE_TARGETS[riskTier];

  let pool;
  if (riskTier === 'safe') {
    pool = legs.filter(l => l.risk === 'safe').sort((a, b) => b.prob - a.prob);
  } else if (riskTier === 'medium') {
    const med  = legs.filter(l => l.risk === 'medium').sort((a, b) => b.prob - a.prob);
    const safe = legs.filter(l => l.risk === 'safe').sort((a, b) => a.prob - b.prob);
    pool = [...med, ...safe];
  } else {
    const high = legs.filter(l => l.risk === 'high').sort((a, b) => b.prob - a.prob);
    const med  = legs.filter(l => l.risk === 'medium').sort((a, b) => a.prob - b.prob);
    pool = [...high, ...med];
  }

  const selected = [];
  const usedGameKeys = new Set();
  const typeCounts = { Moneyline: 0, Spread: 0, Total: 0 };

  // Pass 1: fill each type to target, no repeat game keys
  for (const type of ['Moneyline', 'Spread', 'Total']) {
    const want = targets[type] ?? 0;
    for (const leg of pool) {
      if (typeCounts[type] >= want) break;
      if (leg.type !== type) continue;
      if (usedGameKeys.has(leg.gameKey)) continue;
      selected.push(leg);
      usedGameKeys.add(leg.gameKey);
      typeCounts[type]++;
    }
  }

  // Pass 2: fill remaining slots with any unused game
  for (const leg of pool) {
    if (selected.length >= numLegs) break;
    if (usedGameKeys.has(leg.gameKey)) continue;
    selected.push(leg);
    usedGameKeys.add(leg.gameKey);
  }

  if (selected.length < 2) return null;

  const decimalProduct = selected.reduce((acc, l) => acc * toDecimal(l.odds), 1);
  const parlayOdds = decimalProduct >= 2
    ? Math.round((decimalProduct - 1) * 100)
    : Math.round(-100 / (decimalProduct - 1));
  const combinedProb = selected.reduce((acc, l) => acc * l.prob, 1);

  return { legs: selected, parlayOdds, combinedProb, decimalProduct, riskTier };
}

const TIER_CONFIG = {
  safe:   { emoji: '🛡️', color: 0x2ecc71, label: 'SAFE PARLAY',        legCount: 3 },
  medium: { emoji: '⚡',  color: 0xf39c12, label: 'MEDIUM RISK PARLAY', legCount: 4 },
  high:   { emoji: '🔥',  color: 0xe74c3c, label: 'HIGH RISK PARLAY',   legCount: 5 },
};

function buildParlayEmbed(parlay, date) {
  const cfg = TIER_CONFIG[parlay.riskTier];
  const parlayOddsStr = parlay.parlayOdds > 0 ? `+${parlay.parlayOdds}` : `${parlay.parlayOdds}`;
  const probPct = (parlay.combinedProb * 100).toFixed(1);
  const payout100 = parlay.parlayOdds > 0
    ? `$${parlay.parlayOdds.toLocaleString()}`
    : `$${(10000 / Math.abs(parlay.parlayOdds)).toFixed(2)}`;

  const picksValue = parlay.legs.map((l, i) =>
    `\`${i + 1}.\` ${l.label}\n` +
    `    └ Odds: \`${fmtOdds(l.odds)}\` · Prob: \`${(l.prob * 100).toFixed(1)}%\` · ${l.type} · ${l.sportName}`
  ).join('\n');

  return new EmbedBuilder()
    .setColor(cfg.color)
    .setTitle(`${cfg.emoji} ${cfg.label}`)
    .setDescription(`**${date}** · ${parlay.legs.length}-leg parlay`)
    .addFields(
      { name: '📋 Picks', value: picksValue },
      { name: '💰 Parlay Odds',          value: `\`${parlayOddsStr}\``, inline: true },
      { name: '📈 Combined Probability', value: `\`${probPct}%\``,      inline: true },
      { name: '💵 $100 Bet Pays',        value: `\`${payout100}\``,     inline: true },
    )
    .setFooter({ text: '⚠️ For entertainment only. Gamble responsibly. 18+ only.' })
    .setTimestamp();
}

function buildHeaderEmbed(date) {
  return new EmbedBuilder()
    .setColor(0x2c3e50)
    .setTitle('🎯 Daily Parlay Report')
    .setDescription(
      `**${date}**\n\nToday's best parlay picks across NBA, NFL, MLB, NCAAB & NCAAF.\n` +
      `Odds sourced live · Mix of moneylines, spreads & totals.\n\n` +
      `🛡️ **Safe** — Heavy favorites, low volatility\n` +
      `⚡ **Medium** — Balanced risk/reward\n` +
      `🔥 **High Risk** — Upsets & long shots`
    )
    .setTimestamp();
}

async function buildDailyParlayEmbed() {
  const games = await fetchAllOdds();
  const legs  = extractLegs(games);
  const date  = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });

  console.log(`[parlayEngine] ${legs.length} legs | ML:${legs.filter(l=>l.type==='Moneyline').length} Spread:${legs.filter(l=>l.type==='Spread').length} Total:${legs.filter(l=>l.type==='Total').length}`);

  const embeds = [buildHeaderEmbed(date)];

  for (const tier of ['safe', 'medium', 'high']) {
    const parlay = buildParlay(legs, tier, TIER_CONFIG[tier].legCount);
    if (parlay) {
      console.log(`[${tier}] ${parlay.legs.map(l => l.type).join(', ')}`);
      embeds.push(buildParlayEmbed(parlay, date));
    }
  }

  if (embeds.length === 1) {
    embeds.push(new EmbedBuilder().setColor(0x95a5a6).setTitle('😴 No Games Found')
      .setDescription('No games with available odds in the next 72 hours.'));
  }

  return embeds;
}

module.exports = { buildDailyParlayEmbed, buildParlay, extractLegs, buildParlayEmbed, TIER_CONFIG };
