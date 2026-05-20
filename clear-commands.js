require('dotenv').config();
const { REST, Routes } = require('discord.js');

const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_TOKEN);

(async () => {
  try {
    console.log('🗑️  Clearing ALL guild commands...');
    await rest.put(
      Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
      { body: [] }
    );
    console.log('✅ All guild commands cleared!');
    console.log('Now run: npm run deploy');
  } catch (err) {
    console.error('❌ Error:', err.message);
  }
})();
