#!/usr/bin/env bash
# Build release binary and optional DMG (Phase 9)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AETHER_DIR="$ROOT/macos/Aether"
BUILD_DIR="$ROOT/macos/build"
APP_NAME="Aether"
APP="$BUILD_DIR/$APP_NAME.app"
DMG="$BUILD_DIR/$APP_NAME.dmg"
APP_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-only) APP_ONLY=true; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$BUILD_DIR"
cd "$AETHER_DIR"
swift build -c release

BIN="$AETHER_DIR/.build/release/Aether"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"
cp "$AETHER_DIR/Info.plist" "$APP/Contents/Info.plist"
cp "$AETHER_DIR/Aether.entitlements" "$APP/Contents/Resources/" 2>/dev/null || true

echo "Built $APP"

if $APP_ONLY; then
  exit 0
fi

VOL="/Volumes/$APP_NAME"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "DMG: $DMG"
echo "Next: macos/scripts/sign-and-notarize.sh \"$APP\""
