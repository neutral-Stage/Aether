#!/bin/bash
# Run ONCE inside the golden benchmark VM, in its GUI session (Terminal), from
# the Aether checkout. See docs/BENCHMARK_VM.md. It:
#   1. installs the bench LaunchAgent that starts the sidecar at login,
#   2. touches every app the benchmark checks use, so macOS asks for
#      Automation permission now (click Allow for each) instead of mid-run,
#   3. runs the doctor so you can see what is still missing.
set -euo pipefail

AETHER="$(cd "$(dirname "$0")/../.." && pwd)"
AGENT="$HOME/Library/LaunchAgents/com.aether.bench.sidecar.plist"

[[ -x "$AETHER/.venv/bin/python" ]] || {
  echo "No $AETHER/.venv. Create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.lock" >&2
  exit 1
}

mkdir -p "$HOME/Library/LaunchAgents"
sed "s#__AETHER__#${AETHER}#g" "$AETHER/scripts/vm/com.aether.bench.sidecar.plist" > "$AGENT"
launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT"
echo "Installed and started $AGENT"

echo "Asking each app once; click Allow on every Automation prompt."
for app in Finder "System Events" Safari Notes Reminders Calendar TextEdit Calculator "System Settings"; do
  osascript -e "tell application \"$app\" to get name" >/dev/null 2>&1 || true
  osascript -e "tell application \"System Events\" to get name of every process whose name is \"$app\"" >/dev/null 2>&1 || true
done
osascript -e 'tell application "Finder" to get name of every window' >/dev/null 2>&1 || true
osascript -e 'tell application "Notes" to get name of every note' >/dev/null 2>&1 || true
osascript -e 'tell application "Reminders" to get name of every list' >/dev/null 2>&1 || true
osascript -e 'tell application "Calendar" to get name of every calendar' >/dev/null 2>&1 || true
osascript -e 'tell application "Safari" to get name of every window' >/dev/null 2>&1 || true
osascript -e 'tell application "System Events" to get name of every process' >/dev/null 2>&1 || true
for app in Safari Notes Reminders Calendar TextEdit Calculator "System Settings"; do
  osascript -e "tell application \"$app\" to quit" >/dev/null 2>&1 || true
done

sleep 3
"$AETHER/.venv/bin/python" -m aether.app --doctor || true
curl -fsS -H "Authorization: Bearer aether-bench" http://127.0.0.1:8765/health >/dev/null \
  && echo "Sidecar answers on 127.0.0.1:8765." \
  || echo "Sidecar is not answering yet: see /tmp/aether-bench-sidecar.log"
echo "Done. Shut the VM down; it is now the golden image."
