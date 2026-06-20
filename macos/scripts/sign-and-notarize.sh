#!/usr/bin/env bash
# Sign and notarize Aether.app (Phase 9) — see macos/SIGNING.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$ROOT/macos/build/Aether.app}"
IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Your Name (TEAMID)}"
ENTITLEMENTS="$ROOT/macos/Aether/Aether.entitlements"
ZIP="${2:-$ROOT/macos/build/Aether.zip}"

if [[ ! -d "$APP" ]]; then
  echo "App bundle not found: $APP" >&2
  echo "Build first: macos/scripts/build-dmg.sh --app-only" >&2
  exit 1
fi

echo "Signing $APP ..."
codesign --force --deep --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  "$APP"
codesign --verify --verbose=2 "$APP"

echo "Creating archive for notarization ..."
ditto -c -k --keepParent "$APP" "$ZIP"

if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
  echo "Submitting to notarytool ..."
  xcrun notarytool submit "$ZIP" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "${NOTARY_PASSWORD:-@keychain:AC_PASSWORD}" \
    --wait
  xcrun stapler staple "$APP"
  echo "Stapled notarization ticket."
else
  echo "Skip notarization (set APPLE_ID, APPLE_TEAM_ID, NOTARY_PASSWORD)."
fi

echo "Done: $APP"
