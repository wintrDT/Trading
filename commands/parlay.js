// commands/parlay.js
const { SlashCommandBuilder } = require('discord.js');
const { buildDailyParlayEmbed } = require('../utils/parlayEngine');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('parlay')
    .setDescription('Get today\'s best parlay recommendations')
    .addStringOption(opt =>
      opt.setName('tier')
        .setDescription('Risk tier to show (default: all)')
        .addChoices(
          { name: 'Safe picks',        value: 'safe'   },
          { name: 'Medium Risk picks', value: 'medium' },
          { name: 'High Risk picks',   value: 'high'   },
          { name: 'All Tiers',         value: 'all'    },
        )
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const tier = interaction.options.getString('tier') ?? 'all';

    try {
      const embeds = await buildDailyParlayEmbed();

      if (tier === 'all') {
        await interaction.editReply({ embeds });
      } else {
        // embeds[0] is header, [1]=safe, [2]=medium, [3]=high
        const tierIndex = { safe: 1, medium: 2, high: 3 }[tier];
        const targetEmbed = embeds[tierIndex];
        if (!targetEmbed) {
          return interaction.editReply({ content: `⚠️ No ${tier} picks available right now.` });
        }
        await interaction.editReply({ embeds: [embeds[0], targetEmbed] });
      }
    } catch (err) {
      console.error('Parlay command error:', err);
      await interaction.editReply({
        content: '❌ Failed to fetch odds. Check your `ODDS_API_KEY` or try again shortly.',
      });
    }
  },
};
