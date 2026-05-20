const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('help')
    .setDescription('Show all available commands'),

  async execute(interaction) {
    const embed = new EmbedBuilder()
      .setColor(0x00b388)
      .setTitle('🎯 Parlay Bot — Command Reference')
      .setDescription('AI-powered sports picks, live odds, player stats & Kalshi prediction markets')
      .addFields(
        {
          name: '📊 Odds & Schedule',
          value: '`/odds [sport]` · `/schedule`',
        },
        {
          name: '🎯 Sportsbook Parlays',
          value: '`/parlay` · `/parlay tier:safe` · `/parlay tier:medium` · `/parlay tier:high`',
        },
        {
          name: '🏆 Player Stats',
          value: '`/stats [sport] [player]` · `/leaders [sport]`',
        },
        {
          name: '🤖 AI Predictions',
          value: '`/predict [sport] [team]`',
        },
        {
          name: '🟢 Kalshi Prediction Markets',
          value: [
            '`/kalshi-markets sports` — Browse open sports markets',
            '`/kalshi-markets search [query]` — Search markets',
            '`/kalshi-markets detail [ticker]` — Full orderbook + details',
            '`/kalshi-markets value` — Find mispriced markets',
            '`/kalshi-parlay` — Multi-leg position recommendations',
            '`/kalshi-portfolio balance` — Account balance',
            '`/kalshi-portfolio positions` — Open positions',
            '`/kalshi-portfolio orders` — Resting orders',
            '`/kalshi-portfolio history` — Trade history',
            '`/kalshi-order buy [ticker] [side] [contracts]` — Place order',
            '`/kalshi-order cancel [order_id]` — Cancel order',
          ].join('\n'),
        },
        {
          name: '📡 Sports Covered',
          value: '🏀 NBA · 🏈 NFL · ⚾ MLB · 🏀 NCAAB · 🏈 NCAAF',
        },
      )
      .setFooter({ text: '⚠️ For entertainment only. Kalshi is a regulated market. 18+ · Gamble responsibly.' })
      .setTimestamp();

    await interaction.reply({ embeds: [embed], ephemeral: true });
  },
};
