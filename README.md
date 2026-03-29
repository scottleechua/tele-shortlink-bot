# tele-shortlink-bot

A [Telegram](https://telegram.org/) bot for creating [Short.io](https://short.io) shortlinks, with native support for [Pod.link](https://pod.link) podcast episode URLs.

## Features

- 🎙 Create universal [Pod.link](https://pod.link) shortlinks for your podcast episodes
- 📋 Manage multiple [Short.io](https://short.io) domains across multiple accounts
- 🔒 Grant other Telegram users access to your bot
- 🔑 API keys encrypted at rest
- 💾 SQLite persistence via Railway volume

## Setup

### 1. Create a Telegram bot

Talk to [@BotFather](https://t.me/botfather), create a bot, copy the token.

### 2. Get your Telegram user ID

Talk to [@userinfobot](https://t.me/userinfobot) to get your numeric user ID.

### 3. Generate an encryption key

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the output as `ENCRYPTION_KEY` in your `.env` and Railway Variables.

### 4. Local development

```bash
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, and ENCRYPTION_KEY
uv sync
uv run --env-file .env python bot.py
```

### 5. Deploy to Railway

1. Push this repo to GitHub and create a new Railway project from the repo
2. Add a **Volume** in Railway, mount path: `/data`
3. Set these service variables in Railway:
   - `TELEGRAM_BOT_TOKEN`
   - `ADMIN_USER_ID`
   - `ENCRYPTION_KEY`
   - `DB_PATH` → `/data/bot.db`
4. Deploy.

## First run

1. Send `/start` — you'll get the reply keyboard
2. Tap **☰ Menu** → **🌐 Domains** to add your first Short.io domain (you'll need your Short.io private API key)
3. Tap **☰ Menu** → **🎧 Podcasts** to add podcasts and map them to domains
4. Tap **☰ Menu** → **👥 Users** to allow other Telegram users access

## How it works

After `/start`, the bot shows a persistent reply keyboard with two buttons:

- **🔗 New link** — start the shortlink creation flow
- **☰ Menu** — open the inline menu (Domains, Podcasts, Users)

All navigation is through inline buttons. Slash commands (`/domains`, `/podcasts`, `/users`) work too, as does `/cancel` to abort any active flow.

### Creating a shortlink

Tap **🔗 New link**, then choose:

- **🎧 Podcast episode** — pick a saved podcast, pick an episode from the RSS feed, confirm or type a slug. The destination URL is automatically built as a `pod.link` URL.
- **🔗 Any URL** — paste a URL, pick a domain, type a slug. The bot fetches the page title in the background to use as the link title.

The bot syncs your existing links from Short.io in the background when you start, so it can detect slug collisions before you submit. If a slug is already taken, it shows you the existing link and prompts for a different one.

On success, the bot shows the short URL with a **📋 Click to copy** button.

For podcast episodes, the bot auto-suggests `sXXeXX` slugs (e.g. `s02e04`) when season and episode numbers are detectable from the RSS feed. You can confirm the suggestion or type a different slug.

## Commands

| Command | Who | Description |
|---|---|---|
| `/start` | All users | Show the reply keyboard |
| `/domains` | All users | Manage Short.io domains |
| `/podcasts` | All users | Manage saved podcasts |
| `/users` | Admin only | Manage allowed users |
| `/cancel` | All users | Cancel current flow |

## Managing domains (`/domains`)

- **View links** — browse all links on a domain (paginated, synced live from Short.io)
- **Add domain** — paste a Short.io private API key; the bot fetches all domains on that account and lets you select which ones to add
- **Edit domain nickname** — rename a domain's display name
- **Remove domain** — delete a domain

Get your **private** key (not the public key) from [Short.io settings](https://app.short.io/settings/integrations/api-key).

## Managing podcasts (`/podcasts`)

- **Add podcast** — paste an Apple Podcasts URL (e.g. `https://podcasts.apple.com/us/podcast/my-show/id1669984779`); the bot looks up the podcast name and RSS feed via the iTunes API, then asks which domain to associate it with
- **Edit podcast nickname** — rename a podcast's display name
- **Remove podcast** — delete a podcast
