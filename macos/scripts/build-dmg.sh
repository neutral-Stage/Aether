#!/usr/bin/env bash
# Build Aether.app (see build-app.sh) and wrap it in a DMG.
#   macos/scripts/build-dmg.sh [--app-only] [build-app.sh options…]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/macos/build"
APP="$BUILD_DIR/Aether.app"
DMG="$BUILD_DIR/Aether.dmg"
APP_ONLY=false
PASS=()
for arg in "$@"; do
  if [[ "$arg" == "--app-only" ]]; then APP_ONLY=true; else PASS+=("$arg"); fi
done

"$ROOT/macos/scripts/build-app.sh" ${PASS[@]+"${PASS[@]}"}
if $APP_ONLY; then
  exit 0
fi
rm -f "$DMG"
hdiutil create -volname "Aether" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "DMG: $DMG"
echo "For a notarized build: macos/scripts/sign-and-notarize.sh \"$APP\" (needs a Developer ID)"
