# 🎯 Parlay Bot — Discord Sports Prediction Bot

AI-powered Discord bot that delivers **daily parlay picks**, **live odds**, and **AI game predictions** for NBA, NFL, MLB, NCAA Men's & Women's Basketball, and NCAA Football.

---

## ✨ Features

| Command | Description |
|---------|-------------|
| `/parlay` | Full daily parlay report across 3 risk tiers |
| `/parlay tier:safe` | 🛡️ 3-leg safe parlay (heavy favorites) |
| `/parlay tier:medium` | ⚡ 4-leg medium risk parlay |
| `/parlay tier:high` | 🔥  5-leg high risk parlay (big payouts) |
| `/odds [sport]` | Live moneylines, spreads & totals |
| `/schedule` | All games in the next 24 hours |
| `/predict [sport] [team]` | Claude AI game analysis + best bet |
| `/help` | Command reference |

**Auto-post**: Daily parlay report posts at 10 AM every morning in your configured channel.

---

## 🚀 Setup Guide

### Step 1 — Create Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → name it (e.g. "Parlay Bot")
3. Go to **Bot** tab → click **Add Bot**
4. Under **Privileged Gateway Intents**, enable:
   - ✅ Message Content Intent
5. Copy your **Bot Token** (keep it secret!)
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
7. Copy the generated URL and open it to invite the bot to your server

### Step 2 — Get API Keys

**The Odds API** (free tier: 500 requests/month):
1. Sign up at [the-odds-api.com](https://the-odds-api.com)
2. Copy your API key from the dashboard

**Anthropic API** (for /predict command):
1. Sign up at [console.anthropic.com](https://console.anthropic.com)
2. Create an API key

### Step 3 — Configure Environment

```bash
# Clone / download the bot files
cd parlay-bot

# Copy the example env file
cp .env.example .env

# Edit .env with your keys
nano .env   # or use any text editor
```

Fill in all values in `.env`:
```
DISCORD_TOKEN=       # Your bot token
CLIENT_ID=           # Application ID (Developer Portal → General Info)
GUILD_ID=            # Right-click your server → Copy Server ID
ODDS_API_KEY=        # From the-odds-api.com
ANTHROPIC_API_KEY=   # From console.anthropic.com
PARLAY_CHANNEL_ID=   # Right-click target channel → Copy Channel ID
```

> **To get IDs**: Open Discord → Settings → Advanced → Enable **Developer Mode**

### Step 4 — Install & Run

```bash
# Install dependencies
npm install

# Register slash commands with Discord (run once)
npm run deploy

# Start the bot
npm start

# Or for development with auto-restart
npm run dev
```

---

## 📊 How Parlays Are Built

The bot analyzes all upcoming games and scores each potential bet leg:

| Tier | Implied Probability | Strategy |
|------|--------------------|--------------------|
| 🛡️ **Safe** | 65%+ | Top 3 highest-probability bets |
| ⚡ **Medium** | 50–65% | Balanced risk/reward mix |
| 🔥 **High Risk** | <50% | Contrarian picks + upsets |

- Legs from the **same game** are never combined in one parlay
- Best available odds are sourced from **DraftKings, FanDuel, BetMGM**, and more
- Combined probability and payout for a $100 bet is shown for each parlay

---

## 🌐 Hosting (Keep Bot Online 24/7)

### Free Options
- **Railway** — [railway.app](https://railway.app) — Easy deploys, free tier
- **Render** — [render.com](https://render.com) — Free background workers
- **Fly.io** — [fly.io](https://fly.io) — Generous free tier

### Paid / VPS
- **DigitalOcean** — $4/mo droplet
- **Hetzner** — Cheapest reliable VPS

### Quick Railway Deploy
```bash
# Install Railway CLI
npm i -g @railway/cli

railway login
railway new
railway up

# Set env vars in Railway dashboard
```

---

## 📁 Project Structure

```
parlay-bot/
├── bot.js                  # Main entry point, event handling, auto-post scheduler
├── deploy-commands.js      # Register slash commands with Discord
├── package.json
├── .env.example
├── commands/
│   ├── parlay.js           # /parlay — daily parlay picks
│   ├── odds.js             # /odds — live odds by sport
│   ├── predict.js          # /predict — AI game prediction
│   ├── schedule.js         # /schedule — today's games
│   └── help.js             # /help
└── utils/
    ├── parlayEngine.js     # Core parlay analysis & embed builder
    ├── oddsApi.js          # The Odds API integration + caching
    └── cache.js            # In-memory TTL cache
```

---

## ⚠️ Disclaimer

This bot is for **entertainment purposes only**. It does not constitute financial or betting advice. Always gamble responsibly. Must be 18+ or legal gambling age in your jurisdiction.

---

## 🔧 Customization

**Change auto-post time** — Edit the cron schedule in `bot.js`:
```js
schedule.schedule('0 10 * * *', ...)  // 10:00 AM daily
// '0 9 * * *'  = 9 AM
// '0 10 * * 0' = 10 AM Sundays only
```

**Change parlay leg count** — Edit `TIER_CONFIG` in `utils/parlayEngine.js`:
```js
safe:   { legCount: 3 },   // 3-leg parlay
medium: { legCount: 4 },   // 4-leg parlay
high:   { legCount: 5 },   // 5-leg parlay
```
