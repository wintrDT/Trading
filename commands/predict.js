// commands/predict.js
// Uses Claude API to generate a smart prediction for any matchup

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { fetchOddsForSport, SPORT_MAP } = require('../utils/oddsApi');
const Anthropic = require('@anthropic-ai/sdk');

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

module.exports = {
  data: new SlashCommandBuilder()
    .setName('predict')
    .setDescription('Get an AI prediction for an upcoming game')
    .addStringOption(opt =>
      opt.setName('sport')
        .setDescription('Sport')
        .setRequired(true)
        .addChoices(
          { name: 'NBA Basketball', value: 'basketball_nba' },
          { name: 'NFL Football', value: 'americanfootball_nfl' },
          { name: 'MLB Baseball', value: 'baseball_mlb' },
          { name: 'NCAA Mens Basketball', value: 'basketball_ncaab' },
          { name: 'NCAA Womens Basketball', value: 'basketball_ncaaw' },
          { name: 'NCAA Football', value: 'americanfootball_ncaaf' },
        )
    )
    .addStringOption(opt =>
      opt.setName('team')
        .setDescription('Team name to find their next game')
        .setRequired(true)
    ),

  async execute(interaction) {
    await interaction.deferReply();

    const sportKey = interaction.options.getString('sport');
    const teamQuery = interaction.options.getString('team').toLowerCase();
    const meta = SPORT_MAP[sportKey];

    try {
      const games = await fetchOddsForSport(sportKey);
      const game = games.find(g =>
        g.home_team.toLowerCase().includes(teamQuery) ||
        g.away_team.toLowerCase().includes(teamQuery)
      );

      if (!game) {
        return interaction.editReply({
          content: `🔍 No upcoming **${meta.name}** game found matching "${teamQuery}".`,
        });
      }

      const { home_team, away_team, bookmakers, commence_time } = game;
      const gameDate = new Date(commence_time).toLocaleString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric',
        hour: 'numeric', minute: '2-digit',
      });

      // Gather odds context
      const homeML = getBestOdds(bookmakers, 'h2h', home_team);
      const awayML = getBestOdds(bookmakers, 'h2h', away_team);
      const oddsContext = homeML && awayML
        ? `Current moneylines: ${home_team} ${homeML > 0 ? '+' : ''}${homeML}, ${away_team} ${awayML > 0 ? '+' : ''}${awayML}.`
        : 'No moneyline odds available.';

      // Claude prediction
      const prompt = `You are an expert sports analyst. Provide a concise prediction for this ${meta.name} game:

${away_team} @ ${home_team}
Date: ${gameDate}
${oddsContext}

Respond with:
1. **Winner Prediction** (1 sentence with confidence %)
2. **Key Factors** (2-3 bullet points max)
3. **Best Bet** (moneyline, spread, or total — 1 sentence)
4. **Risk Level**: Safe / Medium / High

Keep total response under 200 words. Be direct and analytical.`;

      const aiResponse = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 400,
        messages: [{ role: 'user', content: prompt }],
      });

      const prediction = aiResponse.content[0]?.text ?? 'Unable to generate prediction.';

      const embed = new EmbedBuilder()
        .setColor(0x9b59b6)
        .setTitle(`🤖 AI Prediction: ${away_team} @ ${home_team}`)
        .setDescription(`📅 ${gameDate}`)
        .addFields(
          { name: `${meta.emoji} ${meta.name} Analysis`, value: prediction },
          { name: '📊 Market Odds', value: `${home_team}: \`${fmt(homeML)}\` | ${away_team}: \`${fmt(awayML)}\`` }
        )
        .setFooter({ text: '🤖 AI prediction for entertainment only. Not financial advice.' })
        .setTimestamp();

      await interaction.editReply({ embeds: [embed] });
    } catch (err) {
      console.error('Predict command error:', err);
      await interaction.editReply({ content: '❌ Prediction failed. Try again shortly.' });
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
