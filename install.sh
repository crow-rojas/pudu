#!/usr/bin/env bash
# Installs Pudú: a launchd agent (starts at login, restarts if it dies) plus ~/Applications/Pudú.app.
# `./install.sh uninstall` removes both. `PORT=8766 ./install.sh` serves on another port.
set -euo pipefail

PORT="${PORT:-8765}"
# why: a second copy can be installed side by side (e.g. to test this script) without replacing the live one.
NAME="${PUDU_NAME:-pudu}"
URL="http://localhost:$PORT"
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/Pudú.app"
LOG="$HOME/Library/Logs/$NAME.log"
DOMAIN="gui/$(id -u)"

die() { echo "$*" >&2; exit 1; }
xml() { printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }

[[ "$PORT" =~ ^[0-9]+$ ]] || die "PORT must be a number, got '$PORT'"

plist() {
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$(xml "$LABEL")</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$PY")</string>
    <string>$(xml "$DIR/server.py")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(xml "$SVC_PATH")</string>
    <key>PORT</key><string>$PORT</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG")</string>
</dict>
</plist>
XML
}

# why: a launcher, not a wrapper — the server lives in launchd, so the app only opens a tab and exits.
build_app() {
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp "$DIR/pudu.icns" "$APP/Contents/Resources/pudu.icns"
  cat > "$APP/Contents/Info.plist" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Pudú</string>
  <key>CFBundleIdentifier</key><string>$(xml "$LABEL").app</string>
  <key>CFBundleExecutable</key><string>pudu</string>
  <key>CFBundleIconFile</key><string>pudu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
XML
  printf '#!/bin/sh\nopen "%s"\n' "$URL" > "$APP/Contents/MacOS/pudu"
  chmod +x "$APP/Contents/MacOS/pudu"
  touch "$APP"
}

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -rf "$PLIST" "$APP"
  echo "Pudú uninstalled"
  exit 0
fi

command -v python3 >/dev/null || die "Missing python3 (brew install python)"
command -v gh >/dev/null || die "Missing gh (brew install gh)"
gh auth status >/dev/null 2>&1 || die "gh has no session: run 'gh auth login'"
python3 "$DIR/server.py" --test >/dev/null
# why: launchd doesn't get your shell PATH (pyenv, mise, ~/.local/bin), so pin what the checks just used.
PY="$(command -v python3)"
SVC_PATH="$(dirname "$(command -v gh)"):/usr/local/bin:/usr/bin:/bin"

# why: if something else holds the port, KeepAlive would restart a crashing server forever.
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  die "Port $PORT is already in use by another program. Stop it or pick another port: PORT=8766 ./install.sh"
fi

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
plist > "$PLIST.new"
plutil -lint "$PLIST.new" >/dev/null
# why: unload only now, after every check passed, so a failed reinstall leaves the working agent up.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
mv "$PLIST.new" "$PLIST"
tries=0
# why: bootstrap fails with error 5 while the old agent is still shutting down.
until launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; do
  (( ++tries < 20 )) || die "launchctl bootstrap failed; run ./install.sh again in a few seconds"
  sleep 0.5
done
build_app

echo "Pudú installed: $URL (app in ~/Applications/Pudú.app, logs in ~/Library/Logs/$NAME.log)"
