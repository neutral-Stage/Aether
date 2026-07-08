#!/usr/bin/env bash
# Stamp the repo VERSION into Info.plist so the Swift app, sidecar, and DMG
# can never drift again (Phase 7). Run before build-dmg.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VER="$(tr -d '[:space:]' < "$ROOT/VERSION")"
PLIST="$ROOT/macos/Aether/Info.plist"

/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VER" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VER" "$PLIST"
echo "Stamped VERSION=$VER into $PLIST"
