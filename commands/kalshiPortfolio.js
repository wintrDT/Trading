// commands/kalshiPortfolio.js
// View your Kalshi account: balance, positions, open orders, trade history

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getBalance, getPositions, getOrders, getTrades, getFills, priceToDisplay } = require('../kalshi/kalshiClient');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('kalshi-portfolio')
    .setDescription('View your Kalshi account')
    .addSubcommand(sub =>
      sub.setName('balance')
        .setDescription('Account balance and portfolio value')
    )
    .addSubcommand(sub =>
      sub.setName('positions')
        .setDescription('Your current open positions')
    )
    .addSubcommand(sub =>
      sub.setName('orders')
        .setDescription('Your open resting orders')
    )
    .addSubcommand(sub =>
      sub.setName('history')
        .setDescription('Recent trade history and fills')
    ),

  async execute(interaction) {
    await interaction.deferReply({ ephemeral: true }); // Private — financial data

    const sub = interaction.options.getSubcommand();

    try {
      if (sub === 'balance')   await handleBalance(interaction);
      if (sub === 'positions') await handlePositions(interaction);
      if (sub === 'orders')    await handleOrders(interaction);
      if (sub === 'history')   await handleHistory(interaction);
    } catch (err) {
      console.error('kalshi-portfolio error:', err.message);
      await interaction.editReply({
        content: `❌ Kalshi API error: ${err.message}\n\nMake sure \`KALSHI_EMAIL\` + \`KALSHI_PASSWORD\` (or API key) are set in your \`.env\``,
      });
    }
  },
};

// ── Balance ───────────────────────────────────────────────────────────────────
async function handleBalance(interaction) {
  const data = await getBalance();
  const balance = data?.balance ?? {};

  const available  = ((balance.available_balance ?? 0) / 100).toFixed(2);
  const portfolio  = ((balance.portfolio_value   ?? 0) / 100).toFixed(2);
  const total      = ((balance.total_balance     ?? 0) / 100).toFixed(2);
  const pnl        = ((balance.pnl               ?? 0) / 100).toFixed(2);
  const pnlEmoji   = parseFloat(pnl) >= 0 ? '📈' : '📉';

  const embed = new EmbedBuilder()
    .setColor(0x00b388)
    .setTitle('💼 Kalshi Portfolio Balance')
    .addFields(
      { name: '💵 Available Cash',   value: `\`$${available}\``,  inline: true },
      { name: '📊 Portfolio Value',  value: `\`$${portfolio}\``,  inline: true },
      { name: '🏦 Total Balance',    value: `\`$${total}\``,      inline: true },
      { name: `${pnlEmoji} P&L`,     value: `\`$${pnl}\``,        inline: true },
    )
    .setFooter({ text: 'Kalshi · Data is live from your account' })
    .setTimestamp();

  await interaction.editReply({ embeds: [embed] });
}

// ── Positions ─────────────────────────────────────────────────────────────────
async function handlePositions(interaction) {
  const data = await getPositions({ limit: 20 });
  const positions = data?.market_positions || data?.positions || [];

  if (!positions.length) {
    return interaction.editReply({ content: '📭 No open positions.' });
  }

  const embed = new EmbedBuilder()
    .setColor(0x3498db)
    .setTitle(`📊 Open Positions (${positions.length})`)
    .setFooter({ text: 'Kalshi · Live positions' })
    .setTimestamp();

  let totalValue = 0;

  for (const pos of positions.slice(0, 10)) {
    const yesContracts = pos.position ?? pos.yes_position ?? 0;
    const noContracts  = pos.no_position ?? 0;
    const ticker       = pos.ticker ?? pos.market_ticker ?? 'N/A';
    const title        = pos.market?.title || ticker;
    const currentPrice = pos.market?.yes_bid ?? pos.current_price ?? null;
    const avgPrice     = pos.average_yes_price ?? pos.average_price ?? null;

    const contracts = yesContracts > 0 ? `✅ YES ×${yesContracts}` : `❌ NO ×${Math.abs(noContracts)}`;
    const posValue  = currentPrice != null ? ((yesContracts > 0 ? currentPrice : 100 - currentPrice) / 100 * Math.abs(yesContracts > 0 ? yesContracts : noContracts)).toFixed(2) : '?';
    const avgStr    = avgPrice != null ? `Avg: \`${avgPrice}¢\`` : '';
    const curStr    = currentPrice != null ? `Now: \`${currentPrice}¢\`` : '';

    totalValue += parseFloat(posValue) || 0;

    embed.addFields({
      name: title.slice(0, 60),
      value: [
        `\`${ticker}\` · ${contracts}`,
        [avgStr, curStr].filter(Boolean).join('  ·  '),
        `Est. Value: \`$${posValue}\``,
      ].join('\n'),
    });
  }

  if (positions.length > 10) {
    embed.addFields({ name: '➕ More', value: `${positions.length - 10} more positions not shown` });
  }

  embed.addFields({ name: '💰 Est. Total Position Value', value: `\`$${totalValue.toFixed(2)}\`` });

  await interaction.editReply({ embeds: [embed] });
}

// ── Orders ────────────────────────────────────────────────────────────────────
async function handleOrders(interaction) {
  const data = await getOrders({ status: 'resting', limit: 20 });
  const orders = data?.orders || [];

  if (!orders.length) {
    return interaction.editReply({ content: '📭 No open resting orders.' });
  }

  const embed = new EmbedBuilder()
    .setColor(0xf39c12)
    .setTitle(`📋 Open Orders (${orders.length})`)
    .setFooter({ text: 'Kalshi · Use /kalshi-order cancel to cancel an order' })
    .setTimestamp();

  for (const order of orders.slice(0, 8)) {
    const side      = order.side === 'yes' ? '✅ YES' : '❌ NO';
    const price     = order.yes_price ?? order.price ?? '?';
    const remaining = order.remaining_count ?? order.count ?? '?';
    const created   = order.created_time
      ? new Date(order.created_time).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      : '';

    embed.addFields({
      name: order.ticker ?? 'N/A',
      value: [
        `${side} · Limit \`${price}¢\` · Qty: \`${remaining}\``,
        `Order ID: \`${order.id ?? 'N/A'}\`${created ? `  ·  ${created}` : ''}`,
      ].join('\n'),
    });
  }

  await interaction.editReply({ embeds: [embed] });
}

// ── History ───────────────────────────────────────────────────────────────────
async function handleHistory(interaction) {
  const [tradesData, fillsData] = await Promise.all([
    getTrades({ limit: 10 }),
    getFills({ limit: 10 }),
  ]);

  const trades = tradesData?.trades || [];
  const fills  = fillsData?.fills   || [];

  const embed = new EmbedBuilder()
    .setColor(0x9b59b6)
    .setTitle('📜 Recent Trade History')
    .setFooter({ text: 'Kalshi · Last 10 trades' })
    .setTimestamp();

  if (!trades.length && !fills.length) {
    embed.setDescription('No recent trades found.');
    return interaction.editReply({ embeds: [embed] });
  }

  const items = [...trades, ...fills]
    .sort((a, b) => new Date(b.created_time ?? b.time) - new Date(a.created_time ?? a.time))
    .slice(0, 10);

  let totalPnl = 0;

  for (const item of items) {
    const ticker = item.ticker ?? item.market_ticker ?? 'N/A';
    const side   = item.side === 'yes' ? '✅ YES' : '❌ NO';
    const count  = item.count ?? item.contracts ?? '?';
    const price  = item.yes_price ?? item.price ?? '?';
    const ts     = item.created_time ?? item.time
      ? new Date(item.created_time ?? item.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
      : '';
    const pnl    = item.profit_loss != null ? `P&L: \`$${(item.profit_loss / 100).toFixed(2)}\`` : '';
    if (item.profit_loss) totalPnl += item.profit_loss;

    embed.addFields({
      name: ticker,
      value: [
        `${side} · \`${count}\` contracts @ \`${price}¢\``,
        [ts, pnl].filter(Boolean).join('  ·  '),
      ].join('\n'),
    });
  }

  if (totalPnl !== 0) {
    const pnlStr = (totalPnl / 100).toFixed(2);
    embed.addFields({
      name: `${totalPnl >= 0 ? '📈' : '📉'} Period P&L`,
      value: `\`$${pnlStr}\``,
    });
  }

  await interaction.editReply({ embeds: [embed] });
}
