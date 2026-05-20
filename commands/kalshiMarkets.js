// commands/kalshiMarkets.js
// Browse, search, and view Kalshi prediction markets

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { searchMarkets, getSportsMarkets, getMarket, getOrderbook, priceToDisplay } = require('../kalshi/kalshiClient');
const { scoreMarket, findValueMarkets } = require('../kalshi/kalshiAnalyzer');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('kalshi-markets')
    .setDescription('Browse Kalshi prediction markets')
    .addSubcommand(sub =>
      sub.setName('sports')
        .setDescription('View open sports prediction markets')
        .addStringOption(opt =>
          opt.setName('filter')
            .setDescription('Filter by sport or keyword (e.g. "NBA", "Super Bowl")')
            .setRequired(false)
        )
    )
    .addSubcommand(sub =>
      sub.setName('search')
        .setDescription('Search for a specific market')
        .addStringOption(opt =>
          opt.setName('query')
            .setDescription('Search term (e.g. "Lakers", "World Series")')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('detail')
        .setDescription('Get full details + orderbook for a market')
        .addStringOption(opt =>
          opt.setName('ticker')
            .setDescription('Market ticker (e.g. KXNBA-25APR23-T220)')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('value')
        .setDescription('Find potentially mispriced / high-value markets')
    ),

  async execute(interaction) {
    await interaction.deferReply();
    const sub = interaction.options.getSubcommand();

    try {
      if (sub === 'sports') {
        await handleSports(interaction);
      } else if (sub === 'search') {
        await handleSearch(interaction);
      } else if (sub === 'detail') {
        await handleDetail(interaction);
      } else if (sub === 'value') {
        await handleValue(interaction);
      }
    } catch (err) {
      console.error('kalshi-markets error:', err.message);
      await interaction.editReply({
        content: `❌ Kalshi API error: ${err.message}\nMake sure your credentials are set in \`.env\``,
      });
    }
  },
};

// ── Sports Markets ────────────────────────────────────────────────────────────
async function handleSports(interaction) {
  const filter = interaction.options.getString('filter')?.toLowerCase() || null;
  let markets = await getSportsMarkets();

  if (filter) {
    markets = markets.filter(m =>
      m.title?.toLowerCase().includes(filter) ||
      m.ticker?.toLowerCase().includes(filter)
    );
  }

  if (!markets.length) {
    return interaction.editReply({ content: '😴 No open sports markets found right now. Try `/kalshi-markets search`.' });
  }

  const embed = new EmbedBuilder()
    .setColor(0x00b388)
    .setTitle('🎯 Kalshi Sports Markets')
    .setDescription(`${markets.length} open markets${filter ? ` matching "${filter}"` : ''}`)
    .setFooter({ text: 'Kalshi · Regulated Prediction Markets · kalshi.com' })
    .setTimestamp();

  for (const market of markets.slice(0, 8)) {
    const yesPrice = market.yes_bid ?? market.last_price ?? 50;
    const price    = priceToDisplay(yesPrice);
    const vol      = market.volume != null ? `Vol: ${market.volume.toLocaleString()}` : '';
    const oi       = market.open_interest != null ? `OI: ${market.open_interest.toLocaleString()}` : '';
    const close    = market.close_time
      ? `Closes: ${new Date(market.close_time).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
      : '';

    embed.addFields({
      name: `${market.title || market.ticker}`,
      value: [
        `\`${market.ticker}\``,
        `YES: \`${price.yes}\`  NO: \`${price.no}\`  Implied: \`${price.pct}\``,
        [vol, oi, close].filter(Boolean).join('  ·  '),
      ].join('\n'),
    });
  }

  if (markets.length > 8) {
    embed.addFields({ name: '➕ More Markets', value: `${markets.length - 8} more — use \`/kalshi-markets search\` to narrow down` });
  }

  await interaction.editReply({ embeds: [embed] });
}

// ── Search ────────────────────────────────────────────────────────────────────
async function handleSearch(interaction) {
  const query = interaction.options.getString('query');
  const markets = await searchMarkets(query, 6);

  if (!markets.length) {
    return interaction.editReply({ content: `🔍 No markets found for **"${query}"**. Try a different term.` });
  }

  const embed = new EmbedBuilder()
    .setColor(0x00b388)
    .setTitle(`🔍 Kalshi Search: "${query}"`)
    .setDescription(`${markets.length} result${markets.length !== 1 ? 's' : ''}`)
    .setFooter({ text: 'Use /kalshi-markets detail [ticker] for full orderbook' })
    .setTimestamp();

  for (const market of markets) {
    const yesPrice = market.yes_bid ?? market.last_price ?? 50;
    const price    = priceToDisplay(yesPrice);
    const score    = scoreMarket(market);

    embed.addFields({
      name: market.title || market.ticker,
      value: [
        `Ticker: \`${market.ticker}\``,
        `YES: \`${price.yes}\` · NO: \`${price.no}\` · Prob: \`${price.pct}\``,
        `Liquidity Score: \`${score.liquidity.toFixed(0)}/100\`  Conviction: \`${score.conviction.toFixed(0)}/100\``,
      ].join('\n'),
    });
  }

  await interaction.editReply({ embeds: [embed] });
}

// ── Market Detail + Orderbook ─────────────────────────────────────────────────
async function handleDetail(interaction) {
  const ticker = interaction.options.getString('ticker').toUpperCase();

  const [marketData, obData] = await Promise.all([
    getMarket(ticker),
    getOrderbook(ticker, 5),
  ]);

  const market = marketData?.market;
  if (!market) return interaction.editReply({ content: `❌ Market \`${ticker}\` not found.` });

  const yesPrice = market.yes_bid ?? market.last_price ?? 50;
  const price    = priceToDisplay(yesPrice);
  const ob       = obData?.orderbook;

  const closeDate = market.close_time
    ? new Date(market.close_time).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
    : 'N/A';

  const embed = new EmbedBuilder()
    .setColor(0x00b388)
    .setTitle(`📊 ${market.title || ticker}`)
    .setDescription(market.subtitle || market.rules_primary || '_No description_')
    .addFields(
      { name: '💰 Current Price', value: `YES: \`${price.yes}\`  NO: \`${price.no}\``, inline: true },
      { name: '📈 Implied Prob',  value: `\`${price.pct}\``, inline: true },
      { name: '📅 Closes',        value: `\`${closeDate}\``, inline: true },
      { name: '📊 Volume',        value: `\`${(market.volume ?? 0).toLocaleString()}\``, inline: true },
      { name: '🔓 Open Interest', value: `\`${(market.open_interest ?? 0).toLocaleString()}\``, inline: true },
      { name: '🏷️ Ticker',        value: `\`${ticker}\``, inline: true },
    )
    .setFooter({ text: 'kalshi.com · Regulated prediction market' })
    .setTimestamp();

  // Orderbook
  if (ob) {
    const yesBids  = (ob.yes  || []).slice(0, 5).map(l => `\`${l.price}¢ × ${l.delta}\``).join('  ');
    const noBids   = (ob.no   || []).slice(0, 5).map(l => `\`${l.price}¢ × ${l.delta}\``).join('  ');
    if (yesBids) embed.addFields({ name: '📗 YES Bids (price × qty)', value: yesBids || 'Empty' });
    if (noBids)  embed.addFields({ name: '📕 NO Bids (price × qty)',  value: noBids  || 'Empty' });
  }

  // Payout calc example
  const { totalCost, profit, roi } = calcPayoutDisplay(yesPrice, 10, 'yes');
  embed.addFields({
    name: '💵 Example: 10 YES contracts',
    value: `Cost: \`$${totalCost}\` · Profit if correct: \`$${profit}\` · ROI: \`${roi}%\``,
  });

  await interaction.editReply({ embeds: [embed] });
}

// ── Value Markets ─────────────────────────────────────────────────────────────
async function handleValue(interaction) {
  const markets = await getSportsMarkets();
  const valueMarkets = findValueMarkets(markets, 6);

  if (!valueMarkets.length) {
    return interaction.editReply({ content: '📊 No clear value markets identified right now.' });
  }

  const embed = new EmbedBuilder()
    .setColor(0xf39c12)
    .setTitle('💡 Kalshi Value Markets')
    .setDescription('High-conviction, lower-volume markets that may be mispriced')
    .setFooter({ text: 'Not financial advice. DYOR.' })
    .setTimestamp();

  for (const market of valueMarkets) {
    const price = priceToDisplay(market.yesPrice);
    const side  = market.yesPrice > 50 ? '✅ YES' : '❌ NO';
    embed.addFields({
      name: market.title || market.ticker,
      value: [
        `Ticker: \`${market.ticker}\``,
        `Price: \`${price.yes}\` YES / \`${price.no}\` NO · Lean: ${side}`,
        `Conviction: \`${market.conviction.toFixed(0)}/50\` · Vol: \`${(market.volume ?? 0).toLocaleString()}\``,
      ].join('\n'),
    });
  }

  await interaction.editReply({ embeds: [embed] });
}

function calcPayoutDisplay(yesPrice, contracts, side) {
  const costPer = (side === 'yes' ? yesPrice : 100 - yesPrice) / 100;
  const total   = contracts * costPer;
  const payout  = contracts * 1.00;
  const profit  = payout - total;
  return {
    totalCost: total.toFixed(2),
    profit: profit.toFixed(2),
    roi: ((profit / total) * 100).toFixed(1),
  };
}
