// commands/leaders.js
// Shows league stat leaders via ESPN API

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const axios = require('axios');
const cache = require('../utils/cache');

const ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports';
const ESPN_SPORTS_STATS = 'https://sports.core.api.espn.com/v2/sports';

const SPORT_CONFIG = {
  nba: {
    sport: 'basketball', league: 'nba', name: 'NBA', emoji: '🏀', color: 0xe56020,
    categories: [
      { label: 'Points',   value: 'points'   },
      { label: 'Rebounds', value: 'rebounds' },
      { label: 'Assists',  value: 'assists'  },
      { label: 'Steals',   value: 'steals'   },
      { label: 'Blocks',   value: 'blocks'   },
      { label: '3-Pointers', value: 'threePointFieldGoalsMade' },
    ],
  },
  nfl: {
    sport: 'football', league: 'nfl', name: 'NFL', emoji: '🏈', color: 0x013369,
    categories: [
      { label: 'Passing Yards', value: 'passingYards'    },
      { label: 'Rushing Yards', value: 'rushingYards'    },
      { label: 'Receiving Yards', value: 'receivingYards' },
      { label: 'Touchdowns',   value: 'totalTouchdowns'  },
      { label: 'Sacks',        value: 'sacks'            },
      { label: 'Interceptions', value: 'interceptions'   },
    ],
  },
  mlb: {
    sport: 'baseball', league: 'mlb', name: 'MLB', emoji: '⚾', color: 0xd50032,
    categories: [
      { label: 'Batting Avg', value: 'avg'          },
      { label: 'Home Runs',   value: 'homeRuns'     },
      { label: 'RBI',         value: 'RBIs'         },
      { label: 'ERA',         value: 'ERA'          },
      { label: 'Strikeouts',  value: 'strikeouts'   },
      { label: 'Stolen Bases', value: 'stolenBases' },
    ],
  },
};

module.exports = {
  data: new SlashCommandBuilder()
    .setName('leaders')
    .setDescription('View league stat leaders')
    .addStringOption(opt =>
      opt.setName('sport')
        .setDescription('Sport')
        .setRequired(true)
        .addChoices(
          { name: 'NBA Basketball', value: 'nba' },
          { name: 'NFL Football',   value: 'nfl' },
          { name: 'MLB Baseball',   value: 'mlb' },
        )
    )
    .addStringOption(opt =>
      opt.setName('category')
        .setDescription('Stat category (e.g. points, rebounds, passingYards)')
        .setRequired(false)
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const sportKey  = interaction.options.getString('sport');
    const category  = interaction.options.getString('category')?.toLowerCase() || null;
    const cfg = SPORT_CONFIG[sportKey];

    try {
      const cacheKey = `leaders_${sportKey}_${category || 'default'}`;
      let data = cache.get(cacheKey);

      if (!data) {
        const { data: resp } = await axios.get(
          `${ESPN_BASE}/${cfg.sport}/${cfg.league}/leaders`,
          { timeout: 10000 }
        );
        data = resp;
        cache.set(cacheKey, data, 30 * 60);
      }

      const allCategories = data?.leaders || [];

      if (!allCategories.length) {
        return interaction.editReply({ content: `📊 No leaders data available for ${cfg.name} right now.` });
      }

      // Find the requested category or show top categories
      let categoriesToShow;
      if (category) {
        const match = allCategories.filter(c =>
          c.name?.toLowerCase().includes(category) ||
          c.displayName?.toLowerCase().includes(category) ||
          c.abbreviation?.toLowerCase().includes(category)
        );
        categoriesToShow = match.length ? match.slice(0, 3) : allCategories.slice(0, 3);
      } else {
        // Show top 3 default categories for the sport
        const defaultCats = cfg.categories.slice(0, 3).map(c => c.value);
        categoriesToShow = allCategories.filter(c => defaultCats.includes(c.name)).slice(0, 3);
        if (!categoriesToShow.length) categoriesToShow = allCategories.slice(0, 3);
      }

      const embed = new EmbedBuilder()
        .setColor(cfg.color)
        .setTitle(`${cfg.emoji} ${cfg.name} Stat Leaders`)
        .setDescription(`Top performers · ${new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`)
        .setFooter({ text: 'Stats via ESPN' })
        .setTimestamp();

      for (const cat of categoriesToShow) {
        const leaders = cat.leaders || [];
        if (!leaders.length) continue;

        const lines = leaders.slice(0, 5).map((entry, i) => {
          const rank = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'][i] || `${i + 1}.`;
          const name = entry.athlete?.displayName || entry.displayName || 'Unknown';
          const team = entry.team?.abbreviation || entry.athlete?.team?.abbreviation || '';
          const val  = entry.displayValue || entry.value || '--';
          return `${rank} **${name}** ${team ? `*(${team})*` : ''} — \`${val}\``;
        });

        embed.addFields({
          name: cat.displayName || cat.name || 'Leaders',
          value: lines.join('\n') || '_No data_',
        });
      }

      // Show available categories as a hint
      const availableNames = allCategories.slice(0, 8).map(c => `\`${c.name}\``).join(', ');
      embed.addFields({
        name: '💡 Other Categories',
        value: availableNames || 'N/A',
      });

      await interaction.editReply({ embeds: [embed] });

    } catch (err) {
      console.error('Leaders command error:', err);
      await interaction.editReply({ content: `❌ Failed to fetch ${cfg.name} leaders. Try again shortly.` });
    }
  },
};
