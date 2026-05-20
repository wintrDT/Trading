require('dotenv').config();
const { Client, GatewayIntentBits, Collection } = require('discord.js');

// Start web dashboard alongside the bot
require('./web/server');
const fs = require('fs');
const path = require('path');

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

// Load commands
client.commands = new Collection();
const commandFiles = fs.readdirSync(path.join(__dirname, 'commands')).filter(f => f.endsWith('.js'));
for (const file of commandFiles) {
  const command = require(`./commands/${file}`);
  client.commands.set(command.data.name, command);
}

// Handle interactions (slash commands + buttons)
client.on('interactionCreate', async (interaction) => {
  // Button handler (for Kalshi order confirmations)
  if (interaction.isButton()) {
    if (interaction.customId.startsWith('kalshi_')) {
      const orderCmd = client.commands.get('kalshi-order');
      if (orderCmd?.handleButton) {
        await orderCmd.handleButton(interaction).catch(console.error);
      }
    }
    return;
  }

  if (!interaction.isChatInputCommand()) return;
  const command = client.commands.get(interaction.commandName);
  if (!command) return;

  try {
    await command.execute(interaction);
  } catch (error) {
    console.error(`Error executing ${interaction.commandName}:`, error);
    const msg = { content: '❌ An error occurred executing that command.', ephemeral: true };
    if (interaction.replied || interaction.deferred) {
      await interaction.followUp(msg);
    } else {
      await interaction.reply(msg);
    }
  }
});

// Auto-post daily parlays at 10 AM
const schedule = require('node-cron');
schedule.schedule('0 10 * * *', async () => {
  const channelId = process.env.PARLAY_CHANNEL_ID;
  if (!channelId) return;
  const channel = client.channels.cache.get(channelId);
  if (!channel) return;

  const { buildDailyParlayEmbed } = require('./utils/parlayEngine');
  const { buildDailyKalshiEmbeds } = require('./commands/kalshiParlay');

  const [sportsbookEmbeds, kalshiEmbeds] = await Promise.all([
    buildDailyParlayEmbed(),
    buildDailyKalshiEmbeds(),
  ]);

  for (const embed of sportsbookEmbeds) await channel.send({ embeds: [embed] });
  for (const embed of kalshiEmbeds)     await channel.send({ embeds: [embed] });
  console.log('📬 Auto-posted daily parlays (sportsbook + Kalshi)');
});

// Check pending picks every 2 hours and post results when they resolve
schedule.schedule('0 */2 * * *', async () => {
  const channelId = process.env.PARLAY_CHANNEL_ID;
  if (!channelId) return;
  const channel = client.channels.cache.get(channelId);
  if (!channel) return;

  const { checkPendingPicks } = require('./utils/pickTracker');
  const { EmbedBuilder }      = require('discord.js');

  const resolved = await checkPendingPicks();
  for (const pick of resolved) {
    const won      = pick.result === 'win';
    const profitN  = won
      ? parseFloat(pick.totalPayout) - parseFloat(pick.totalCost)
      : -parseFloat(pick.totalCost);
    const profitStr = profitN >= 0 ? `+$${profitN.toFixed(2)}` : `-$${Math.abs(profitN).toFixed(2)}`;
    const legLines  = pick.legs.map(l => {
      const icon = l.result === 'win' ? '✅' : l.result === 'loss' ? '❌' : '⏳';
      const title = (l.title || l.ticker).slice(0, 60);
      return `${icon} **${l.side.toUpperCase()}** · ${title}`;
    }).join('\n');

    const embed = new EmbedBuilder()
      .setColor(won ? 0x2ecc71 : 0xe74c3c)
      .setTitle(won ? '✅ Parlay Hit!' : '❌ Parlay Lost')
      .setDescription(`**${pick.tier}** · ${pick.date}`)
      .addFields(
        { name: 'Legs', value: legLines },
        { name: 'Result', value: `${profitStr} · ROI was ${pick.roi}%`, inline: true },
      )
      .setFooter({ text: 'Kalshi Pick Tracker' })
      .setTimestamp();

    await channel.send({ embeds: [embed] });
  }
});

client.once('ready', () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
  console.log(`📊 Loaded ${client.commands.size} commands`);
  client.user.setActivity('📈 Kalshi Markets', { type: 3 });
});

client.login(process.env.DISCORD_TOKEN);
