#!/usr/bin/env bash
# Install (or refresh) the macOS LaunchAgents that keep the chad-fleet hub running — one
# supervised agent PER service, so a crashed service auto-restarts (KeepAlive) and the hub
# comes up at login. You never type a launch command.
#
#   ./scripts/install-launchagent.sh           install + start all services
#   ./scripts/install-launchagent.sh uninstall  unload + remove all plists
#   ./scripts/install-launchagent.sh status     show launchd + service status
#
# Prereqs (one-time, your secret — never stored in this repo or any plist):
#   security add-generic-password -s chad-fleet-bws-token -a "$USER" -w "$BWS_ACCESS_TOKEN"
# launchd does NOT inherit ~/.zshrc, so the token is read from the Keychain at launch time.
set -euo pipefail

PREFIX="com.chadsimon.chad-fleet"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT/scripts/run-service.sh"
LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

# launchd starts with a bare PATH; spell out where the toolchain lives so uv/bws/node resolve.
TOOL_PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

source "$ROOT/scripts/services.sh"

service_names() {
  local entry
  for entry in "${SERVICES[@]}"; do echo "${entry%%|*}"; done
}

write_plist() {
  local name="$1" label="$PREFIX.$1" plist="$LA_DIR/$PREFIX.$1.plist"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${RUNNER}</string>
    <string>${name}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>PATH</key>
    <string>${TOOL_PATH}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <!-- run-service.sh runs the service in the foreground, so launchd watches the real
       process. KeepAlive restarts it if it ever exits (crash or clean). -->
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>/tmp/chad-fleet-${name}.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/chad-fleet-${name}.log</string>
</dict>
</plist>
PLIST
  echo "$plist"
}

cmd="${1:-install}"

case "$cmd" in
  install)
    [ -x "$RUNNER" ] || chmod +x "$RUNNER"
    mkdir -p "$LA_DIR"
    for name in $(service_names); do
      echo "wrote plist: $(write_plist "$name")"
    done

    # Don't load until the BWS token is resolvable — otherwise each agent's RunAtLoad fires,
    # run-service.sh exits 1 (no secrets), and KeepAlive retry-loops every ThrottleInterval.
    TOKEN="${BWS_ACCESS_TOKEN:-}"
    if [ -z "$TOKEN" ]; then
      TOKEN="$(security find-generic-password -w -s chad-fleet-bws-token -a "$USER" 2>/dev/null || true)"
    fi
    if [ -z "$TOKEN" ]; then
      cat >&2 <<MSG

NOT activated yet — the Bitwarden token isn't in your Keychain.
Run this ONCE (your secret; reads straight from your existing env, you don't type it):

    security add-generic-password -s chad-fleet-bws-token -a "\$USER" -w "\$BWS_ACCESS_TOKEN"

then re-run:  ./scripts/install-launchagent.sh
After that all services auto-start at login and auto-restart on crash. No launch command, ever.
MSG
      exit 0
    fi

    for name in $(service_names); do
      label="$PREFIX.$name"
      plist="$LA_DIR/$label.plist"
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      launchctl bootstrap "$DOMAIN" "$plist"
      launchctl enable "$DOMAIN/$label"
      launchctl kickstart -k "$DOMAIN/$label"
      echo "started: $label"
    done
    echo "logs: /tmp/chad-fleet-<service>.log   verify: ./launch.sh status   (or http://localhost:3000)"
    ;;

  uninstall)
    for name in $(service_names); do
      label="$PREFIX.$name"
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      rm -f "$LA_DIR/$label.plist"
      echo "removed: $label"
    done
    ;;

  status)
    echo "== launchd =="
    for name in $(service_names); do
      label="$PREFIX.$name"
      state="$(launchctl print "$DOMAIN/$label" 2>/dev/null | grep -E "^[[:space:]]*state =" | head -1 | xargs || echo "not loaded")"
      printf "  %-28s %s\n" "$label" "$state"
    done
    echo "== services =="
    "$ROOT/launch.sh" status
    ;;

  *)
    echo "usage: $0 {install|uninstall|status}" >&2
    exit 2
    ;;
esac
