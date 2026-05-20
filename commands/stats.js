// commands/stats.js
// Fetches individual player stats via ESPN + BallDontLie APIs

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const {
  searchPlayerESPN,
  getPlayerStatsESPN,
  getPlayerOverviewESPN,
  getNBAStatsBDL,
  ESPN_SPORT_MAP,
} = require('../utils/statsApi');
const {
  formatNBAStats,
  formatNFLStats,
  formatMLBStats,
  formatBDLStats,
} = require('../utils/statsFormatter');

const SPORT_COLORS = {
  nba:   0xe56020,
  nfl:   0x013369,
  mlb:   0xd50032,
  ncaab: 0xff6b00,
  ncaaf: 0x006400,
};

module.exports = {
  data: new SlashCommandBuilder()
    .setName('stats')
    .setDescription('Look up individual player stats')
    .addStringOption(opt =>
      opt.setName('sport')
        .setDescription('Sport')
        .setRequired(true)
        .addChoices(
          { name: 'NBA Basketball', value: 'nba' },
          { name: 'NFL Football', value: 'nfl' },
          { name: 'MLB Baseball', value: 'mlb' },
          { name: 'NCAA Mens Basketball', value: 'ncaab' },
          { name: 'NCAA Football', value: 'ncaaf' },
        )
    )
    .addStringOption(opt =>
      opt.setName('player')
        .setDescription('Player name (e.g. "LeBron James", "Patrick Mahomes")')
        .setRequired(true)
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const sportKey = interaction.options.getString('sport');
    const playerName = interaction.options.getString('player');
    const meta = ESPN_SPORT_MAP[sportKey];

    try {
      // For NBA, try BallDontLie first (more reliable structure)
      if (sportKey === 'nba') {
        const bdlResult = await getNBAStatsBDL(playerName);
        if (bdlResult) {
          const formatted = formatBDLStats(bdlResult);
          if (formatted) {
            const embed = buildPlayerEmbed({
              name: formatted.playerName,
              team: formatted.team,
              position: formatted.position,
              sport: 'NBA',
              sportEmoji: '🏀',
              season: `${formatted.season}-${formatted.season + 1} Season Averages`,
              fields: formatted.fields,
              color: SPORT_COLORS.nba,
            });
            return interaction.editReply({ embeds: [embed] });
          }
        }
        // Fall through to ESPN if BDL fails
      }

      // ESPN path for all sports
      const athletes = await searchPlayerESPN(playerName, sportKey);

      if (!athletes.length) {
        return interaction.editReply({
          content: `🔍 No player found matching **"${playerName}"** in ${meta.name}. Try their full name.`,
        });
      }

      const athlete = athletes[0];
      const athleteId = athlete.id || athlete.$ref?.split('/').pop();

      if (!athleteId) {
        return interaction.editReply({ content: '❌ Could not retrieve player ID from ESPN.' });
      }

      // Fetch overview and stats in parallel
      const [overview, statsData] = await Promise.all([
        getPlayerOverviewESPN(athleteId, sportKey),
        getPlayerStatsESPN(athleteId, sportKey),
      ]);

      const fullName   = overview?.athlete?.fullName || athlete.displayName || playerName;
      const teamName   = overview?.athlete?.team?.displayName || overview?.team?.displayName || 'N/A';
      const position   = overview?.athlete?.position?.displayName || overview?.athlete?.position?.abbreviation || 'N/A';
      const jersey     = overview?.athlete?.jersey ? `#${overview.athlete.jersey}` : '';
      const headshotUrl = overview?.athlete?.headshot?.href || null;
      const experience = overview?.athlete?.experience?.years != null
        ? `Year ${overview.athlete.experience.years + 1}`
        : null;

      // Format stats based on sport
      let statFields = [];
      if (statsData) {
        if (sportKey === 'nba' || sportKey === 'ncaab') {
          statFields = formatNBAStats(statsData, fullName);
        } else if (sportKey === 'nfl' || sportKey === 'ncaaf') {
          statFields = formatNFLStats(statsData, fullName, position);
        } else if (sportKey === 'mlb') {
          statFields = formatMLBStats(statsData);
        }
      }

      if (!statFields.length) {
        statFields = [{
          name: '📊 Stats',
          value: '_No current season stats available yet._',
        }];
      }

      const descParts = [`**${teamName}** · ${position}${jersey ? ` · ${jersey}` : ''}`];
      if (experience) descParts.push(experience);

      const embed = buildPlayerEmbed({
        name: fullName,
        team: teamName,
        position,
        sport: meta.name,
        sportEmoji: meta.emoji,
        season: '2024–25 Season',
        fields: statFields,
        color: SPORT_COLORS[sportKey] ?? 0x2c3e50,
        thumbnail: headshotUrl,
        description: descParts.join(' · '),
      });

      await interaction.editReply({ embeds: [embed] });

    } catch (err) {
      console.error('Stats command error:', err);
      await interaction.editReply({
        content: `❌ Failed to fetch stats for **${playerName}**. Try their full name or check the sport selection.`,
      });
    }
  },
};

function buildPlayerEmbed({ name, sport, sportEmoji, season, fields, color, thumbnail, description }) {
  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${sportEmoji} ${name}`)
    .setDescription(description || season)
    .addFields(...fields)
    .setFooter({ text: `${sport} · Stats via ESPN & BallDontLie` })
    .setTimestamp();

  if (thumbnail) embed.setThumbnail(thumbnail);

  return embed;
}
