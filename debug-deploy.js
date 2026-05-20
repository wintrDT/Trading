require('dotenv').config();
const fs = require('fs');
const path = require('path');

const commandFiles = fs.readdirSync('./commands').filter(f => f.endsWith('.js')).sort();
const commands = [];

for (const file of commandFiles) {
  try {
    delete require.cache[require.resolve('./commands/' + file)];
    const cmd = require('./commands/' + file);
    const json = cmd.data.toJSON();
    commands.push({ file, name: json.name, json });
  } catch(e) {
    console.log('❌ LOAD ERROR in', file, ':', e.message);
  }
}

console.log(`Loaded ${commands.length} commands\n`);
commands.forEach((c, i) => {
  console.log(`[${i}] ${c.file} -> "${c.name}"`);
});

console.log('\n--- Full JSON of command at index 3 ---');
console.log(JSON.stringify(commands[3]?.json, null, 2));

// Check all for issues
console.log('\n--- Checking all commands for issues ---');
for (const { file, json } of commands) {
  // Check description length
  if (json.description?.length > 100) {
    console.log(`❌ ${file}: description too long (${json.description.length}): ${json.description}`);
  }
  // Check options
  for (const opt of json.options || []) {
    if (opt.description?.length > 100) {
      console.log(`❌ ${file} option "${opt.name}": description too long (${opt.description.length})`);
    }
    for (const choice of opt.choices || []) {
      if (choice.name?.length > 100) console.log(`❌ ${file} choice name too long: ${choice.name}`);
      if (/[^\x00-\x7F]/.test(choice.name)) console.log(`❌ ${file} choice has non-ASCII: ${choice.name}`);
    }
    // Check subcommand options too
    for (const sub of opt.options || []) {
      if (sub.description?.length > 100) {
        console.log(`❌ ${file} subcommand option "${sub.name}": description too long (${sub.description.length}): ${sub.description}`);
      }
    }
  }
}
console.log('Done checking');
