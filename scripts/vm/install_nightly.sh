#!/usr/bin/env bash
# Install (or remove) the nightly VM evaluation as a launchd agent on the host Mac.
#
#   scripts/vm/install_nightly.sh [--at HH:MM] [--python /path/to/python]
#   scripts/vm/install_nightly.sh --uninstall
#
# Needs the golden image from docs/BENCHMARK_VM.md. Runs at 03:17 by default,
# skips nights on battery power, and logs to ~/Library/Logs/Aether/.
set -euo pipefail

LABEL="com.aether.nightly-eval"
AETHER="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs/Aether"
AT="03:17"
PYTHON="$AETHER/.venv/bin/python"
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --at) AT="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$UNINSTALL" = 1 ]; then
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
fi

if ! [[ "$AT" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "--at needs HH:MM (24-hour), got: $AT" >&2
  exit 2
fi
HOUR=$((10#${BASH_REMATCH[1]}))
MINUTE=$((10#${BASH_REMATCH[2]}))
if [ ! -x "$PYTHON" ]; then
  echo "Python not found at $PYTHON (use --python)." >&2
  exit 1
fi
command -v lume >/dev/null 2>&1 || echo "warning: lume is not on PATH; see docs/BENCHMARK_VM.md" >&2

mkdir -p "$LOGDIR" "$(dirname "$PLIST")"
sed -e "s|__AETHER__|$AETHER|g" -e "s|__PYTHON__|$PYTHON|g" -e "s|__LOGDIR__|$LOGDIR|g" \
    -e "s|__HOUR__|$HOUR|g" -e "s|__MINUTE__|$MINUTE|g" \
    "$AETHER/scripts/vm/$LABEL.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
printf 'Installed %s: every night at %02d:%02d. Log: %s/nightly-eval.log\n' \
  "$LABEL" "$HOUR" "$MINUTE" "$LOGDIR"
echo "Run it now with: launchctl kickstart gui/$(id -u)/$LABEL"
