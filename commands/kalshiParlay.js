// commands/kalshiParlay.js
const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { generateParlays } = require('../kalshi/parlayService');

const DISCORD_TIER = {
  safe:   { emoji: '🛡️', color: 0x2ecc71, label: 'SAFE PICKS' },
  medium: { emoji: '⚡',  color: 0xf39c12, label: 'MEDIUM RISK PICKS' },
  high:   { emoji: '🔥',  color: 0xe74c3c, label: 'HIGH RISK PICKS' },
};

module.exports = {
  buildDailyKalshiEmbeds,
  data: new SlashCommandBuilder()
    .setName('kalshi-parlay')
    .setDescription('Kalshi prediction market parlay recommendations')
    .addStringOption(opt =>
      opt.setName('tier')
        .setDescription('Risk tier to show')
        .addChoices(
          { name: 'Safe - high probability picks', value: 'safe'   },
          { name: 'Medium Risk picks',             value: 'medium' },
          { name: 'High Risk picks',               value: 'high'   },
          { name: 'All Tiers',                     value: 'all'    },
        )
    )
    .addStringOption(opt =>
      opt.setName('filter')
        .setDescription('Filter by keyword such as NBA or Celtics')
        .setRequired(false)
    )
    .addBooleanOption(opt =>
      opt.setName('all_markets')
        .setDescription('Include all Kalshi markets not just sports')
        .setRequired(false)
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const tier   = interaction.options.getString('tier') ?? 'all';
    const filter = interaction.options.getString('filter')?.toLowerCase() || null;
    const useAll = interaction.options.getBoolean('all_markets') ?? false;

    try {
      const { results, date, marketCount, priceCount } = await generateParlays({ tier, filter, useAll });

      if (priceCount < 2) {
        return interaction.editReply({ embeds: [buildLowMarketsEmbed(marketCount, priceCount, date, filter)] });
      }

      const embeds = [buildHeaderEmbed(date, marketCount, priceCount, filter)];
      for (const result of results) {
        embeds.push(buildParlayEmbed(result, DISCORD_TIER[result.tier], date));
      }
      if (results.length === 0) {
        embeds.push(buildLowMarketsEmbed(marketCount, priceCount, date, filter));
      }

      await interaction.editReply({ embeds });
    } catch (err) {
      console.error('kalshi-parlay error:', err.message);
      await interaction.editReply({ content: `❌ Error: ${err.message}` });
    }
  },
};

// ── Embed builders ────────────────────────────────────────────────────────────

function buildHeaderEmbed(date, total, withPrices, filter) {
  return new EmbedBuilder()
    .setColor(0x00b388)
    .setTitle('🎯 Kalshi Prediction Market Picks')
    .setDescription(
      `**${date}**\n\n` +
      `Found **${total}** open markets · **${withPrices}** with live prices${filter ? ` · Filter: "${filter}"` : ''}.\n\n` +
      `Each leg = 1 Kalshi contract · Cost = price · Payout = **$1.00** if correct.\n\n` +
      `🛡️ **Safe** — 70–90% probability\n⚡ **Medium** — 50–70%\n🔥 **High Risk** — Underdog (20–50%)`
    )
    .setFooter({ text: 'Kalshi is a regulated exchange. Not financial advice.' })
    .setTimestamp();
}

function buildParlayEmbed(result, cfg, date) {
  const probPct  = result.combinedProb;
  const roiColor = parseFloat(result.roi) > 50 ? '🔥' : parseFloat(result.roi) > 20 ? '📈' : '📊';

  const maxTitleLen = Math.max(60, Math.floor((1000 - result.legs.length * 112) / result.legs.length));

  const picksValue = result.legs.map((m, i) => {
    const side     = m._side === 'yes' ? 'YES' : 'NO';
    const sideProb = m._sideProb;
    const cost     = (sideProb / 100).toFixed(2);
    const roi      = ((1 - sideProb / 100) / (sideProb / 100) * 100).toFixed(0);
    const rawTitle = m._decodedTitle || m.ticker || 'Unknown';
    const legList  = rawTitle.split(', ');
    let title = '';
    for (const leg of legList) {
      const candidate = title ? title + ', ' + leg : leg;
      if (candidate.length > maxTitleLen) { title = title ? title + '…' : leg.slice(0, maxTitleLen) + '…'; break; }
      title = candidate;
    }
    if (!title) title = rawTitle.slice(0, maxTitleLen) + '…';
    const shortTicker = m.ticker
      .replace('KXMVESPORTSMULTIGAMEEXTENDED', 'MVE')
      .replace('KXMVECROSSCATEGORY', 'CROSS')
      .slice(0, 36);
    const edgePart = m._edge != null
      ? ` · Edge \`${m._edge >= 0 ? '+' : ''}${m._edge}%\` vs Vegas`
      : '';
    return `\`${i + 1}.\` ${title}\n    └ **${side}** · Cost \`$${cost}\` · Prob \`${sideProb}%\` · ROI \`+${roi}%\`${edgePart}\n    └ 🔗 \`${shortTicker}\``;
  }).join('\n');

  return new EmbedBuilder()
    .setColor(cfg.color)
    .setTitle(`${cfg.emoji} ${cfg.label}`)
    .setDescription(`**${date}** · ${result.legs.length} positions`)
    .addFields(
      { name: '📋 Positions',               value: picksValue },
      { name: '💰 Total Cost (1 each)',      value: `\`$${result.totalCost}\``,                        inline: true },
      { name: '💵 Payout if All Correct',    value: `\`$${parseFloat(result.totalPayout).toFixed(2)}\``, inline: true },
      { name: '📈 Win Probability',          value: `\`${probPct}%\``,                                 inline: true },
      { name: `${roiColor} ROI if All Correct`, value: `\`${result.roi}%\``,                           inline: true },
      { name: '💡 Profit if All Correct',    value: `\`$${result.profit}\``,                            inline: true },
    )
    .setFooter({ text: 'Kalshi.com · Regulated prediction market · Not financial advice' })
    .setTimestamp();
}

function buildLowMarketsEmbed(marketCount, priceCount, date, filter) {
  return new EmbedBuilder()
    .setColor(0x95a5a6)
    .setTitle('😴 Low Market Activity')
    .setDescription(
      `**${date}** — **${marketCount}** markets found, **${priceCount}** with live prices${filter ? ` for "${filter}"` : ''}.\n\n` +
      `**Try:**\n` +
      `• \`/kalshi-parlay tier:high\` — relaxes probability requirements\n` +
      `• \`/kalshi-parlay all_markets:True\` — includes all categories\n` +
      (filter ? `• Remove the filter to see all sports markets\n` : '') +
      `\nMarkets update as new games are listed.`
    )
    .setFooter({ text: 'Kalshi · Markets update as new games are listed' })
    .setTimestamp();
}

async function buildDailyKalshiEmbeds() {
  const { results, date, marketCount, priceCount } = await generateParlays({ save: true });

  if (priceCount < 2) {
    return [buildLowMarketsEmbed(marketCount, priceCount, date, null)];
  }

  const embeds = [buildHeaderEmbed(date, marketCount, priceCount, null)];
  for (const result of results) {
    embeds.push(buildParlayEmbed(result, DISCORD_TIER[result.tier], date));
  }
  if (results.length === 0) {
    embeds.push(buildLowMarketsEmbed(marketCount, priceCount, date, null));
  }
  return embeds;
}
