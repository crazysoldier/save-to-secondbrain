#!/bin/bash
# Install the Second Brain bot as a macOS launchd service.
# It will start automatically on login and restart on crash.
#
# Usage:
#   ./install-service.sh          # install & start
#   ./install-service.sh uninstall # stop & remove

set -euo pipefail

LABEL="com.secondbrain.bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
BOT="$PROJECT_DIR/bot.py"
LOG="$PROJECT_DIR/bot.log"

if [ "${1:-}" = "uninstall" ]; then
    echo "Stopping and removing $LABEL …"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Done. Bot will no longer autostart."
    exit 0
fi

# Sanity checks
if [ ! -f "$PYTHON" ]; then
    echo "Error: venv not found at $PYTHON"
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "Error: .env not found. Copy .env.example and fill it in first."
    exit 1
fi

# Stop existing instance if running
launchctl unload "$PLIST" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$BOT</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
PLIST

launchctl load "$PLIST"

sleep 2
if launchctl list | grep -q "$LABEL"; then
    echo "Bot installed and running."
    echo "  Logs: tail -f $LOG"
    echo "  Stop: launchctl unload $PLIST"
    echo "  Uninstall: $0 uninstall"
else
    echo "Error: service did not start. Check: tail $LOG"
    exit 1
fi
