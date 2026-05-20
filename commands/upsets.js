// commands/upsets.js
const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getGameMarkets }   = require('../kalshi/kalshiClient');
const { findUpsetMarkets } = require('../kalshi/upsetService');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('upsets')
    .setDescription('Find live games where an upset is in progress on Kalshi'),

  async execute(interaction) {
    await interaction.deferReply();

    try {
      const markets = await getGameMarkets();
      const upsets  = findUpsetMarkets(markets);

      if (upsets.length === 0) {
        return interaction.editReply({
          embeds: [
            new EmbedBuilder()
              .setColor(0x95a5a6)
              .setTitle('🎲 No Live Upsets Detected')
              .setDescription(
                'No game markets with significant price movement found right now.\n\n' +
                'Upsets are flagged when a live game market shifts **12+ percentage points** from its opening price.\n\n' +
                'Try again during active games.'
              )
              .setTimestamp(),
          ],
        });
      }

      const embed = new EmbedBuilder()
        .setColor(0xff4757)
        .setTitle('🚨 Live Upset Alert')
        .setDescription(
          `**${upsets.length}** live game${upsets.length > 1 ? 's' : ''} with big price shifts — potential upset opportunities.\n` +
          `*Shift = change from opening price this session.*`
        )
        .setTimestamp();

      for (const u of upsets) {
        const arrow     = u.shift > 0 ? '📈' : '📉';
        const shiftStr  = u.shift > 0 ? `+${u.shift}¢` : `${u.shift}¢`;
        const side      = u.upsetSide.toUpperCase();
        const title     = u.title.slice(0, 56) + (u.title.length > 56 ? '…' : '');
        const openLabel = `${u.openProb}%`;
        const nowLabel  = `${u.upsetProb}%`;

        embed.addFields({
          name: `${arrow} ${title}`,
          value: [
            `**Bet ${side}** · Was \`${openLabel}\` → Now \`${nowLabel}\` · Shift \`${shiftStr}\``,
            `**ROI:** \`+${u.roi}%\`  ·  🔗 \`${u.ticker.slice(0, 36)}\``,
          ].join('\n'),
        });
      }

      embed.setFooter({ text: 'Flags shifts ≥12¢ from open · Kalshi · Not financial advice' });
      await interaction.editReply({ embeds: [embed] });

    } catch (err) {
      console.error('[/upsets]', err.message);
      await interaction.editReply({ content: `❌ Error: ${err.message}` });
    }
  },
};
