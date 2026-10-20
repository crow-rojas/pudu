#!/usr/bin/env bash
# Installs Pudú as a login service that restarts if it dies, plus a launcher that opens it:
#   macOS: launchd agent + ~/Applications/Pudú.app    Linux: systemd user unit + .desktop entry
# `./install.sh uninstall` removes both. `PORT=8766 ./install.sh` serves on another port.
set -euo pipefail

PORT="${PORT:-8765}"
# why: a second copy can be installed side by side (e.g. to test this script) without replacing the live one.
NAME="${PUDU_NAME:-pudu}"
URL="http://localhost:$PORT"
DIR="$(cd "$(dirname "$0")" && pwd)"

die() { echo "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "Missing $1. Install it: $2"; }
xml() { printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }
# why: systemd expands % specifiers and unquotes \ and " inside quoted ExecStart/Environment values.
sd() { printf '%s' "$1" | sed 's/[\\"]/\\&/g; s/%/%%/g'; }

[[ "$PORT" =~ ^[0-9]+$ ]] || die "PORT must be a number, got '$PORT'"

# --- macOS: launchd ---

LABEL="local.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/Pudú.app"
LOG="$HOME/Library/Logs/$NAME.log"
DOMAIN="gui/$(id -u)"

mac_plist() {
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
mac_app() {
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

mac_running() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

mac_install() {
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  mac_plist > "$PLIST.new"
  plutil -lint "$PLIST.new" >/dev/null
  # why: unload only now, after every check passed, so a failed reinstall leaves the working service up.
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  mv "$PLIST.new" "$PLIST"
  local tries=0
  # why: bootstrap fails with error 5 while the old agent is still shutting down.
  until launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; do
    (( ++tries < 20 )) || die "launchctl bootstrap failed; run ./install.sh again in a few seconds"
    sleep 0.5
  done
  mac_app
}

mac_uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -rf "$PLIST" "$APP"
}

# --- Linux: systemd user unit ---

UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$NAME.service"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$NAME.desktop"

linux_unit() {
  cat <<INI
[Unit]
Description=Pudú, a local GitHub PR inbox

[Service]
ExecStart="$(sd "$PY")" "$(sd "$DIR/server.py")"
Environment=PYTHONUNBUFFERED=1
Environment=PORT=$PORT
Environment="PATH=$(sd "$SVC_PATH")"
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
INI
}

linux_desktop() {
  cat <<INI
[Desktop Entry]
Type=Application
Name=Pudú
Comment=Local GitHub PR inbox
Exec=xdg-open $URL
Icon=$DIR/favicon.svg
Categories=Development;
INI
}

linux_running() { systemctl --user is-active --quiet "$NAME" 2>/dev/null; }

linux_install() {
  systemctl --user show-environment >/dev/null 2>&1 \
    || die "systemctl --user isn't available here: run python3 \"$DIR/server.py\" manually."
  mkdir -p "$(dirname "$UNIT")"
  linux_unit > "$UNIT"
  systemctl --user daemon-reload
  # why: restart rather than enable --now, so a reinstall also picks up a new unit or server.py.
  systemctl --user enable --quiet "$NAME"
  systemctl --user restart "$NAME"
  if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
    OPEN="open $URL in your Windows browser (no launcher on WSL)"
    return
  fi
  mkdir -p "$(dirname "$DESKTOP")"
  linux_desktop > "$DESKTOP"
  if command -v update-desktop-database >/dev/null; then update-desktop-database "$(dirname "$DESKTOP")" || true; fi
}

linux_uninstall() {
  systemctl --user disable --now "$NAME" 2>/dev/null || true
  rm -f "$UNIT" "$DESKTOP"
  systemctl --user daemon-reload 2>/dev/null || true
}

# --- shared ---

case "$(uname -s)" in
  Darwin)
    OS=mac PY_HINT="brew install python" GH_HINT="brew install gh"
    OPEN="Pudú.app in ~/Applications (Spotlight or Dock)" LOGS="tail -f ~/Library/Logs/$NAME.log"
    AUTH_HINT="check 'gh auth status'" ;;
  Linux)
    OS=linux PY_HINT="your package manager (e.g. sudo apt install python3)"
    GH_HINT="https://github.com/cli/cli#installation"
    OPEN="Pudú in your applications menu" LOGS="journalctl --user -u $NAME -f"
    AUTH_HINT="the service may not reach your keyring; run 'gh auth login --insecure-storage' and ./install.sh again" ;;
  *) die "No installer for $(uname -s): run python3 \"$DIR/server.py\" and open $URL." ;;
esac

if [[ "${1:-}" == "uninstall" ]]; then
  "${OS}_uninstall"
  echo "Pudú uninstalled"
  exit 0
fi

need python3 "$PY_HINT"
need gh "$GH_HINT"
gh auth status >/dev/null 2>&1 || die "gh has no session: run 'gh auth login'"
python3 "$DIR/server.py" --test >/dev/null
# why: the service doesn't get your shell PATH (pyenv, mise, ~/.local/bin), so pin what the checks just used.
PY="$(command -v python3)"
SVC_PATH="$(dirname "$(command -v gh)"):/usr/local/bin:/usr/bin:/bin"

# why: if something else holds the port, the service would crash and restart forever.
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && ! "${OS}_running"; then
  die "Port $PORT is already in use by another program. Stop it or pick another port: PORT=8766 ./install.sh"
fi

"${OS}_install"

STATUS="$(python3 - "$PORT" <<'PY' 2>/dev/null || true
import json, sys, time, urllib.request
port = sys.argv[1]
req = urllib.request.Request("http://127.0.0.1:%s/api/data" % port, headers={"Host": "localhost:" + port})
for _ in range(20):
  try:
    d = json.load(urllib.request.urlopen(req, timeout=30))
    break
  except OSError:
    time.sleep(0.5)
else:
  sys.exit(print("down"))
print("auth" if d.get("errorKind") == "gh_auth" or "auth" in (d.get("error") or "").lower() else "ok")
PY
)"
case "$STATUS" in
  ok) ;;
  auth) echo "Warning: Pudú is running but gh can't authenticate from the service: $AUTH_HINT." >&2 ;;
  *) echo "Warning: Pudú didn't answer on port $PORT; check the logs: $LOGS" >&2 ;;
esac

cat <<EOF
Pudú installed: $URL
  Open:   $OPEN
  Logs:   $LOGS
  Claude Code (MCP): claude mcp add --scope user pudu -- python3 "$DIR/server.py" --mcp
EOF
