// commands/odds.js
const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { fetchOddsForSport, SPORT_MAP } = require('../utils/oddsApi');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('odds')
    .setDescription('View live odds for upcoming games')
    .addStringOption(opt =>
      opt.setName('sport')
        .setDescription('Sport to show odds for')
        .setRequired(true)
        .addChoices(
          { name: 'NBA Basketball', value: 'basketball_nba' },
          { name: 'NFL Football', value: 'americanfootball_nfl' },
          { name: 'MLB Baseball', value: 'baseball_mlb' },
          { name: 'NCAA Mens Basketball', value: 'basketball_ncaab' },
          { name: 'NCAA Womens Basketball', value: 'basketball_ncaaw' },
          { name: 'NCAA Football', value: 'americanfootball_ncaaf' },
        )
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const sportKey = interaction.options.getString('sport');
    const meta = SPORT_MAP[sportKey];

    try {
      const games = await fetchOddsForSport(sportKey);

      if (!games.length) {
        return interaction.editReply({
          content: `${meta.emoji} No upcoming **${meta.name}** games with odds right now.`,
        });
      }

      // Show up to 8 games
      const slice = games.slice(0, 8);

      const embed = new EmbedBuilder()
        .setColor(0x3498db)
        .setTitle(`${meta.emoji} ${meta.name} — Live Odds`)
        .setDescription(`Showing ${slice.length} upcoming games`)
        .setTimestamp()
        .setFooter({ text: 'Best available odds across all books' });

      for (const game of slice) {
        const { home_team, away_team, bookmakers, commence_time } = game;
        if (!bookmakers?.length) continue;

        const gameDate = new Date(commence_time).toLocaleString('en-US', {
          weekday: 'short', month: 'short', day: 'numeric',
          hour: 'numeric', minute: '2-digit',
        });

        // Best moneylines
        const homeML = getBestOdds(bookmakers, 'h2h', home_team);
        const awayML = getBestOdds(bookmakers, 'h2h', away_team);

        // Best spread
        const homeSpr = getBestSpread(bookmakers, home_team);
        const awaySpr = getBestSpread(bookmakers, away_team);

        // Total
        const total = getBestTotal(bookmakers);

        const lines = [];
        if (homeML) lines.push(`ML: **${home_team}** \`${fmt(homeML)}\` | **${away_team}** \`${fmt(awayML)}\``);
        if (homeSpr) lines.push(`Spread: \`${homeSpr}\` / \`${awaySpr}\``);
        if (total)   lines.push(`Total: \`${total}\``);

        embed.addFields({
          name: `${away_team} @ ${home_team}`,
          value: `📅 ${gameDate}\n${lines.join('\n') || 'No odds available'}`,
        });
      }

      await interaction.editReply({ embeds: [embed] });
    } catch (err) {
      console.error('Odds command error:', err);
      await interaction.editReply({ content: '❌ Failed to fetch odds. Try again shortly.' });
    }
  },
};

function fmt(odds) {
  if (odds == null) return 'N/A';
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function getBestOdds(bookmakers, market, teamName) {
  let best = null;
  for (const bk of bookmakers) {
    const m = bk.markets?.find(m => m.key === market);
    const o = m?.outcomes?.find(o => o.name === teamName);
    if (o && (best === null || o.price > best)) best = o.price;
  }
  return best;
}

function getBestSpread(bookmakers, teamName) {
  for (const bk of bookmakers) {
    const m = bk.markets?.find(m => m.key === 'spreads');
    const o = m?.outcomes?.find(o => o.name === teamName);
    if (o) return `${teamName} ${o.point > 0 ? '+' : ''}${o.point} (${fmt(o.price)})`;
  }
  return null;
}

function getBestTotal(bookmakers) {
  for (const bk of bookmakers) {
    const m = bk.markets?.find(m => m.key === 'totals');
    if (m?.outcomes?.length >= 2) {
      const over  = m.outcomes.find(o => o.name === 'Over');
      const under = m.outcomes.find(o => o.name === 'Under');
      if (over && under) return `O/U ${over.point} | Over ${fmt(over.price)} / Under ${fmt(under.price)}`;
    }
  }
  return null;
}
