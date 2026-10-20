#!/usr/bin/env bash
# Installs Pudú: a launchd agent (starts at login, restarts if it dies) plus ~/Applications/Pudú.app.
# `./install.sh uninstall` removes both.
set -euo pipefail

LABEL=local.pudu
URL=http://localhost:8765
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/Pudú.app"
DOMAIN="gui/$(id -u)"

plist() {
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>$DIR/server.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/pudu.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/pudu.log</string>
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
  <key>CFBundleIdentifier</key><string>$LABEL.app</string>
  <key>CFBundleExecutable</key><string>pudu</string>
  <key>CFBundleIconFile</key><string>pudu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
XML
  cat > "$APP/Contents/MacOS/pudu" <<SH
#!/bin/sh
open "$URL"
SH
  chmod +x "$APP/Contents/MacOS/pudu"
  touch "$APP"
}

# why: bootstrap fails if the label is already loaded, so reinstalling always unloads first.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true

if [[ "${1:-}" == "uninstall" ]]; then
  rm -rf "$PLIST" "$APP"
  echo "Pudú desinstalado"
  exit 0
fi

command -v python3 >/dev/null || { echo "Falta python3 (brew install python)"; exit 1; }
command -v gh >/dev/null || { echo "Falta gh (brew install gh)"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh no tiene sesión: corre 'gh auth login'"; exit 1; }
python3 "$DIR/server.py" --test >/dev/null

mkdir -p "$(dirname "$PLIST")" "$HOME/Library/Logs"
plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
build_app

echo "Pudú instalado: $URL (app en ~/Applications/Pudú.app, logs en ~/Library/Logs/pudu.log)"
