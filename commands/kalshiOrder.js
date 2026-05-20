// commands/kalshiOrder.js
// Place and cancel Kalshi orders with confirmation step

const {
  SlashCommandBuilder, EmbedBuilder,
  ActionRowBuilder, ButtonBuilder, ButtonStyle,
} = require('discord.js');
const { getMarket, placeOrder, cancelOrder, getOrders, priceToDisplay, calcPayout } = require('../kalshi/kalshiClient');

// Pending confirmations: userId -> orderDetails
const pendingOrders = new Map();

module.exports = {
  data: new SlashCommandBuilder()
    .setName('kalshi-order')
    .setDescription('Place or cancel Kalshi orders')
    .addSubcommand(sub =>
      sub.setName('buy')
        .setDescription('Place a buy order on a Kalshi market')
        .addStringOption(opt =>
          opt.setName('ticker')
            .setDescription('Market ticker (e.g. KXNBA-25APR23-T220)')
            .setRequired(true)
        )
        .addStringOption(opt =>
          opt.setName('side')
            .setDescription('Buy YES or NO')
            .setRequired(true)
            .addChoices(
              { name: 'YES', value: 'yes' },
              { name: 'NO', value: 'no' },
            )
        )
        .addIntegerOption(opt =>
          opt.setName('contracts')
            .setDescription('Number of contracts to buy')
            .setRequired(true)
            .setMinValue(1)
            .setMaxValue(500)
        )
        .addStringOption(opt =>
          opt.setName('type')
            .setDescription('Order type (default: limit)')
            .addChoices(
              { name: 'Limit (specify price)', value: 'limit'  },
              { name: 'Market (best available)', value: 'market' },
            )
        )
        .addIntegerOption(opt =>
          opt.setName('price')
            .setDescription('Limit price in cents (1–99). Required for limit orders.')
            .setMinValue(1)
            .setMaxValue(99)
        )
    )
    .addSubcommand(sub =>
      sub.setName('cancel')
        .setDescription('Cancel an open order')
        .addStringOption(opt =>
          opt.setName('order_id')
            .setDescription('Order ID to cancel (from /kalshi-portfolio orders)')
            .setRequired(true)
        )
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    if (sub === 'buy')    await handleBuy(interaction);
    if (sub === 'cancel') await handleCancel(interaction);
  },

  // Handle button confirmation interactions
  async handleButton(interaction) {
    const userId = interaction.user.id;

    if (interaction.customId === 'kalshi_confirm') {
      const pending = pendingOrders.get(userId);
      if (!pending) return interaction.reply({ content: '⏰ Order confirmation expired. Please try again.', ephemeral: true });

      pendingOrders.delete(userId);
      await interaction.deferUpdate();

      try {
        const result = await placeOrder(
          pending.ticker, pending.side, pending.type,
          pending.contracts, pending.price
        );
        const orderId = result?.order?.id ?? result?.id ?? 'N/A';
        await interaction.editReply({
          content: `✅ Order placed! Order ID: \`${orderId}\`\nUse \`/kalshi-portfolio positions\` to track it.`,
          embeds: [], components: [],
        });
      } catch (err) {
        await interaction.editReply({
          content: `❌ Order failed: ${err.response?.data?.detail ?? err.message}`,
          embeds: [], components: [],
        });
      }
    }

    if (interaction.customId === 'kalshi_cancel_order') {
      pendingOrders.delete(interaction.user.id);
      await interaction.update({ content: '🚫 Order cancelled.', embeds: [], components: [] });
    }
  },
};

// ── Buy Flow ──────────────────────────────────────────────────────────────────
async function handleBuy(interaction) {
  await interaction.deferReply({ ephemeral: true });

  const ticker    = interaction.options.getString('ticker').toUpperCase();
  const side      = interaction.options.getString('side');
  const contracts = interaction.options.getInteger('contracts');
  const type      = interaction.options.getString('type') ?? 'limit';
  const price     = interaction.options.getInteger('price') ?? null;

  if (type === 'limit' && price == null) {
    return interaction.editReply({ content: '❌ Limit orders require a price. Add `price:` option, or use `type:market`.' });
  }

  try {
    const marketData = await getMarket(ticker);
    const market = marketData?.market;
    if (!market) return interaction.editReply({ content: `❌ Market \`${ticker}\` not found.` });

    const yesPrice = market.yes_bid ?? market.last_price ?? 50;
    const noPrice  = 100 - yesPrice;
    const display  = priceToDisplay(yesPrice);

    // Use limit price if provided, else use current market price for calc
    const effectivePrice = price ?? (side === 'yes' ? yesPrice : noPrice);
    const { totalCost, profit, roi } = calcPayoutInfo(effectivePrice, contracts);

    // Store pending order
    pendingOrders.set(interaction.user.id, { ticker, side, type, contracts, price });
    // Auto-expire after 60s
    setTimeout(() => pendingOrders.delete(interaction.user.id), 60_000);

    const sideEmoji = side === 'yes' ? '✅' : '❌';
    const priceStr  = type === 'market' ? 'Market' : `\`${effectivePrice}¢\``;

    const embed = new EmbedBuilder()
      .setColor(0xf39c12)
      .setTitle('⚠️ Confirm Order')
      .setDescription(`Please review and confirm your Kalshi order.`)
      .addFields(
        { name: '🏷️ Market',   value: `${market.title || ticker}\n\`${ticker}\`` },
        { name: '📊 Current Price', value: `YES: \`${display.yes}\`  NO: \`${display.no}\``, inline: true },
        { name: `${sideEmoji} Your Side`, value: `\`${side.toUpperCase()}\``, inline: true },
        { name: '📦 Contracts', value: `\`${contracts}\``, inline: true },
        { name: '💰 Price',     value: priceStr, inline: true },
        { name: '💵 Total Cost', value: `\`$${totalCost}\``, inline: true },
        { name: '💹 Profit if Correct', value: `\`$${profit}\` (ROI: ${roi}%)`, inline: true },
      )
      .setFooter({ text: 'This will place a REAL order on your Kalshi account. You have 60 seconds to confirm.' })
      .setColor(0xe74c3c);

    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId('kalshi_confirm')
        .setLabel('✅ Confirm Order')
        .setStyle(ButtonStyle.Success),
      new ButtonBuilder()
        .setCustomId('kalshi_cancel_order')
        .setLabel('🚫 Cancel')
        .setStyle(ButtonStyle.Secondary),
    );

    await interaction.editReply({ embeds: [embed], components: [row] });
  } catch (err) {
    console.error('kalshi-order buy error:', err.message);
    await interaction.editReply({ content: `❌ Error: ${err.message}` });
  }
}

// ── Cancel Flow ───────────────────────────────────────────────────────────────
async function handleCancel(interaction) {
  await interaction.deferReply({ ephemeral: true });

  const orderId = interaction.options.getString('order_id');

  try {
    await cancelOrder(orderId);
    await interaction.editReply({ content: `✅ Order \`${orderId}\` cancelled successfully.` });
  } catch (err) {
    await interaction.editReply({
      content: `❌ Could not cancel order: ${err.response?.data?.detail ?? err.message}`,
    });
  }
}

function calcPayoutInfo(pricePerContract, contracts) {
  const totalCost = (pricePerContract / 100 * contracts).toFixed(2);
  const payout    = contracts.toFixed(2);
  const profit    = (contracts - parseFloat(totalCost)).toFixed(2);
  const roi       = ((parseFloat(profit) / parseFloat(totalCost)) * 100).toFixed(1);
  return { totalCost, payout, profit, roi };
}
