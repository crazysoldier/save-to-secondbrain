# Save to Second Brain

A Telegram bot that saves articles, websites, tweets, and X (Twitter) posts as clean Obsidian-compatible markdown files.

Send any URL to the bot → it extracts the content → saves a `.md` file directly into your vault.

## Features

- **Articles & websites** — extracts main content via trafilatura, strips ads and navigation
- **Tweets** — fetches full text, author, media, and timestamp via [FxEmbed](https://github.com/FxEmbed/FxEmbed)
- **X Articles** (long-form native X posts) — fetches full article content using your real browser session (login-gated content included)
- **Obsidian frontmatter** — title, url, date, author, source, type, tags
- **Cover image** placed at the top of the note before the heading
- **Heading detection** for X Articles — short standalone lines become `##` headings
- **Footer stripping** — removes X boilerplate ("Want to publish your own Article?")
- Single-user auth guard — only your Telegram ID can use the bot

## Requirements

- Python 3.9+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))
- An Obsidian vault (or any folder where you want `.md` files saved)
- For X Articles: an active X.com login in Brave or Chrome

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/save-to-secondbrain.git
cd save-to-secondbrain

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright's Chromium browser
.venv/bin/playwright install chromium

# 5. Copy and fill in the config
cp .env.example .env
nano .env   # or open in any editor
```

### `.env` fields

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | Your numeric Telegram ID (from @userinfobot) |
| `OBSIDIAN_VAULT_PATH` | Absolute path to your vault, e.g. `/Users/you/Documents/SecondBrain` |
| `OBSIDIAN_SAVE_FOLDER` | Subfolder inside the vault (created automatically). Default: `Saved` |
| `DEFAULT_TAGS` | Comma-separated tags added to every note. Default: `saved,reading` |

## X Article login (one-time)

X Articles are login-gated. The bot uses cookies from your real browser session — no passwords stored, no login window.

Make sure you are **logged in to X.com in Brave or Chrome**, then run:

```bash
.venv/bin/python login.py
```

This saves your session cookies to `session_cookies.json`. Re-run this if X Articles stop working (cookies expire after ~30 days).

## Running the bot

### Foreground

```bash
.venv/bin/python bot.py
```

Press `Ctrl+C` to stop.

### Background (current session only)

```bash
nohup .venv/bin/python bot.py >> bot.log 2>&1 &
echo "Bot PID: $!"
```

Keeps running after you close the terminal. Logs go to `bot.log`.

To stop:
```bash
pkill -f "python bot.py"
```

### Persistent background — survives reboots (macOS launchd)

This is the recommended way to run the bot permanently.

**1. Create the plist** — replace both paths with your actual project path:

```bash
mkdir -p ~/Library/LaunchAgents

cat > ~/Library/LaunchAgents/com.secondbrain.bot.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.secondbrain.bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USERNAME/Projects/save-to-secondbrain/.venv/bin/python</string>
    <string>/Users/YOUR_USERNAME/Projects/save-to-secondbrain/bot.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/YOUR_USERNAME/Projects/save-to-secondbrain</string>
  <key>StandardOutPath</key>
  <string>/Users/YOUR_USERNAME/Projects/save-to-secondbrain/bot.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOUR_USERNAME/Projects/save-to-secondbrain/bot.log</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
EOF
```

**2. Load it** (starts the bot immediately and on every login):

```bash
launchctl load ~/Library/LaunchAgents/com.secondbrain.bot.plist
```

**Managing the service:**

```bash
# Check status
launchctl list | grep secondbrain

# Stop
launchctl unload ~/Library/LaunchAgents/com.secondbrain.bot.plist

# Start / restart
launchctl load ~/Library/LaunchAgents/com.secondbrain.bot.plist

# View logs
tail -f ~/Projects/save-to-secondbrain/bot.log
```

## Note format

```markdown
---
title: Article Title
url: https://...
date: 2026-05-05
author: Author Name
source: site.com
type: article
tags: ["saved", "reading"]
---

![](cover_image_url)

# Article Title

Full article text with ## section headings...
```

## Current limitations

- X Articles require periodic re-login (`python login.py`) as cookies expire (~30 days)
- Playwright Chromium adds ~5 seconds to X Article fetches
- No support for paywalled articles (non-X)
- Images are linked, not downloaded locally

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Telegram bot, message handling |
| `fetcher.py` | URL detection, article/tweet fetching |
| `browser.py` | Playwright-based X Article fetcher |
| `formatter.py` | Builds Obsidian markdown from fetched content |
| `login.py` | Extracts X.com cookies from your real browser |
| `config.py` | Loads settings from `.env` |
