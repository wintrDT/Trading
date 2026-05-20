// commands/schedule.js
const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { fetchAllOdds, SPORT_MAP } = require('../utils/oddsApi');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('schedule')
    .setDescription('View today\'s games across all supported sports'),

  async execute(interaction) {
    await interaction.deferReply();

    try {
      const games = await fetchAllOdds();
      const now = new Date();
      const cutoff = new Date(now.getTime() + 24 * 60 * 60 * 1000);

      // Filter to today's games
      const todayGames = games.filter(g => {
        const t = new Date(g.commence_time);
        return t >= now && t <= cutoff;
      }).sort((a, b) => new Date(a.commence_time) - new Date(b.commence_time));

      if (!todayGames.length) {
        return interaction.editReply({ content: '😴 No games scheduled in the next 24 hours.' });
      }

      // Group by sport
      const bySport = {};
      for (const game of todayGames) {
        const key = game.sportMeta.name;
        if (!bySport[key]) bySport[key] = [];
        bySport[key].push(game);
      }

      const embed = new EmbedBuilder()
        .setColor(0x1abc9c)
        .setTitle('📅 Today\'s Game Schedule')
        .setDescription(`${todayGames.length} games in the next 24 hours`)
        .setTimestamp()
        .setFooter({ text: 'Times shown in your local timezone' });

      for (const [sport, sportGames] of Object.entries(bySport)) {
        const meta = Object.values(SPORT_MAP).find(m => m.name === sport);
        const lines = sportGames.slice(0, 6).map(g => {
          const t = new Date(g.commence_time).toLocaleTimeString('en-US', {
            hour: 'numeric', minute: '2-digit',
          });
          return `\`${t}\` ${g.away_team} @ ${g.home_team}`;
        });
        if (sportGames.length > 6) lines.push(`_...and ${sportGames.length - 6} more_`);

        embed.addFields({ name: `${meta?.emoji ?? '🏆'} ${sport}`, value: lines.join('\n') });
      }

      await interaction.editReply({ embeds: [embed] });
    } catch (err) {
      console.error('Schedule command error:', err);
      await interaction.editReply({ content: '❌ Failed to load schedule. Try again shortly.' });
    }
  },
};
