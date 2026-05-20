const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getStats, loadPicks }               = require('../utils/pickTracker');

const TIER_EMOJI = {
  'SAFE PICKS':        '🛡️',
  'MEDIUM RISK PICKS': '⚡',
  'HIGH RISK PICKS':   '🔥',
};

module.exports = {
  data: new SlashCommandBuilder()
    .setName('kalshi-results')
    .setDescription('Show pick tracker stats and recent parlay history'),

  async execute(interaction) {
    await interaction.deferReply();

    const stats = getStats();

    if (stats.total === 0 && stats.pending === 0) {
      return interaction.editReply({ embeds: [
        new EmbedBuilder()
          .setColor(0x95a5a6)
          .setTitle('📊 Pick Tracker')
          .setDescription('No picks logged yet. Run `/kalshi-parlay` to start tracking.')
          .setFooter({ text: 'Kalshi Pick Tracker' })
          .setTimestamp(),
      ]});
    }

    // ── Overall stats ────────────────────────────────────────────────────────
    const hitRate   = stats.total > 0 ? ((stats.wins / stats.total) * 100).toFixed(1) : '—';
    const profitStr = stats.profit >= 0
      ? `+$${stats.profit.toFixed(2)}`
      : `-$${Math.abs(stats.profit).toFixed(2)}`;

    const overallLines = [
      `**${stats.wins}W / ${stats.losses}L** · ${hitRate}% hit rate · ${profitStr} P&L`,
      stats.pending > 0 ? `⏳ ${stats.pending} pick(s) still pending` : '',
    ].filter(Boolean).join('\n');

    // ── By tier ──────────────────────────────────────────────────────────────
    const tierLines = Object.entries(stats.byTier).map(([tier, t]) => {
      const emoji    = TIER_EMOJI[tier] ?? '📊';
      const total    = t.wins + t.losses;
      const pct      = total > 0 ? ((t.wins / total) * 100).toFixed(0) : '—';
      const pStr     = t.profit >= 0 ? `+$${t.profit.toFixed(2)}` : `-$${Math.abs(t.profit).toFixed(2)}`;
      return `${emoji} **${tier}**: ${t.wins}W/${t.losses}L (${pct}%) · ${pStr}`;
    });

    // ── Recent picks ─────────────────────────────────────────────────────────
    const recentLines = stats.recent.slice(0, 8).map(p => {
      const emoji  = p.result === 'win' ? '✅' : p.result === 'loss' ? '❌' : '⏳';
      const tier   = TIER_EMOJI[p.tier] ?? '📊';
      const payout = p.result === 'win'
        ? `+$${(parseFloat(p.totalPayout) - parseFloat(p.totalCost)).toFixed(2)}`
        : p.result === 'loss'
          ? `-$${parseFloat(p.totalCost)}`
          : 'Pending';
      const legs   = p.legs.length;
      return `${emoji} ${tier} **${p.tier}** · ${p.date} · ${legs} legs · ${payout}`;
    });

    const embed = new EmbedBuilder()
      .setColor(stats.profit >= 0 ? 0x2ecc71 : 0xe74c3c)
      .setTitle('📊 Kalshi Pick Tracker')
      .addFields(
        { name: '🏆 Overall',    value: overallLines || '—' },
        { name: '📈 By Tier',    value: tierLines.join('\n') || '—' },
        { name: '🕐 Recent Picks', value: recentLines.join('\n') || '—' },
      )
      .setFooter({ text: 'Results checked every 2 hours · Kalshi Pick Tracker' })
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });
  },
};
