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

# Keep the app version in lockstep with the repo VERSION file.
"$ROOT/macos/scripts/stamp-version.sh"

cd "$AETHER_DIR"
swift build -c release

BIN="$AETHER_DIR/.build/release/Aether"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"
cp "$AETHER_DIR/Info.plist" "$APP/Contents/Info.plist"
cp "$AETHER_DIR/Aether.entitlements" "$APP/Contents/Resources/" 2>/dev/null || true

# Bundle the Python sidecar so the app is self-contained (no terminal / dev
# checkout needed). SidecarSupervisor prefers Resources/sidecar/aether-sidecar,
# and falls back to `python3 -m sidecar.server` from a dev checkout if absent.
SIDE_DIR="$APP/Contents/Resources/sidecar"
mkdir -p "$SIDE_DIR"
if command -v pyinstaller >/dev/null 2>&1; then
  ( cd "$ROOT" && pyinstaller --noconfirm --onefile --name aether-sidecar \
      --distpath "$SIDE_DIR" --workpath "$BUILD_DIR/pyi" --specpath "$BUILD_DIR/pyi" \
      --collect-submodules aether --collect-submodules sidecar \
      sidecar/__main__.py >/dev/null )
  echo "Bundled frozen sidecar → $SIDE_DIR/aether-sidecar"
else
  echo "WARNING: pyinstaller not found — sidecar NOT bundled." >&2
  echo "         The app will fall back to a dev checkout's python3 -m sidecar.server." >&2
  echo "         For a distributable build: pip install pyinstaller, then re-run." >&2
fi

echo "Built $APP"

if $APP_ONLY; then
  exit 0
fi

VOL="/Volumes/$APP_NAME"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "DMG: $DMG"
echo "Next: macos/scripts/sign-and-notarize.sh \"$APP\""
