#!/usr/bin/env bash
# Build a self-contained Aether.app for this Mac — no Apple Developer account.
#
#   macos/scripts/build-app.sh [--sidecar standalone|venv|pyinstaller|none]
#                              [--sign IDENTITY] [--hardened]
#
# --sidecar standalone (default)  relocatable CPython from uv + site-packages
#                                 inside the bundle. Needs `uv` (brew install uv).
# --sidecar venv                  a --copies venv; depends on the python3.11 that
#                                 built it staying installed.
# --sidecar pyinstaller           frozen --onedir sidecar (needs pyinstaller).
# --sidecar none                  no bundled sidecar; the app runs
#                                 `python3 -m sidecar.server` from a dev checkout.
#
# Signing: by default signs with the "Aether Dev" identity if it exists (create
# it once with macos/scripts/create-dev-identity.sh). A stable identity keeps
# Accessibility / Screen Recording grants across rebuilds; ad-hoc signing ("-")
# changes the code hash every build and macOS forgets the grants.
# --hardened adds the hardened runtime (only needed for notarization).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AETHER_DIR="$ROOT/macos/Aether"
BUILD_DIR="$ROOT/macos/build"
APP="$BUILD_DIR/Aether.app"
SIDECAR_MODE="standalone"
IDENTITY="${CODESIGN_IDENTITY:-Aether Dev}"
HARDENED=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sidecar) SIDECAR_MODE="$2"; shift 2 ;;
    --sign) IDENTITY="$2"; shift 2 ;;
    --hardened) HARDENED=true; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ "$(uname)" != "Darwin" ]]; then
  echo "build-app.sh runs on macOS only." >&2
  exit 1
fi

mkdir -p "$BUILD_DIR"
"$ROOT/macos/scripts/stamp-version.sh"

echo "==> Building Swift app (release)"
( cd "$AETHER_DIR" && swift build -c release )
BIN_DIR="$(cd "$AETHER_DIR" && swift build -c release --show-bin-path)"

echo "==> Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Aether" "$APP/Contents/MacOS/Aether"
cp "$AETHER_DIR/Info.plist" "$APP/Contents/Info.plist"
for bundle in "$BIN_DIR"/*.bundle; do
  [[ -e "$bundle" ]] && cp -R "$bundle" "$APP/Contents/Resources/"
done

SIDE="$APP/Contents/Resources/sidecar"
if [[ "$SIDECAR_MODE" != "none" ]]; then
  echo "==> Bundling sidecar ($SIDECAR_MODE)"
  mkdir -p "$SIDE/app"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' \
    "$ROOT/aether" "$ROOT/sidecar" "$ROOT/configs" "$ROOT/shared" "$ROOT/plugins" "$SIDE/app/"
  cp "$ROOT/config.yaml" "$ROOT/VERSION" "$SIDE/app/"
fi

write_launcher() {
  # $1 = command line (relative to the sidecar dir) that starts the server
  cat > "$SIDE/aether-sidecar" <<LAUNCH
#!/bin/bash
# Started by SidecarSupervisor.swift. State goes to Application Support, never
# into the signed bundle.
DIR="\$(cd "\$(dirname "\$0")" && pwd)"
export AETHER_BUNDLED=1
export PYTHONDONTWRITEBYTECODE=1
USER_CFG="\$HOME/Library/Application Support/Aether/config.yaml"
if [[ -f "\$USER_CFG" ]]; then export AETHER_CONFIG_PATH="\$USER_CFG"; fi
cd "\$DIR/app"
$1
LAUNCH
  chmod +x "$SIDE/aether-sidecar"
}

case "$SIDECAR_MODE" in
  standalone)
    if ! command -v uv >/dev/null 2>&1; then
      echo "uv is required for --sidecar standalone (brew install uv), or use --sidecar venv." >&2
      exit 1
    fi
    uv python install 3.11
    # A uv-managed (python-build-standalone) interpreter is relocatable, unlike
    # a Homebrew/system Python, so it can live inside the bundle.
    PYBIN="$(uv python find --managed-python 3.11)"
    PYHOME="$(dirname "$(dirname "$PYBIN")")"
    if [[ ! -x "$PYHOME/bin/python3" ]]; then
      echo "Could not find a uv-managed CPython 3.11 (got $PYBIN)" >&2
      exit 1
    fi
    rsync -a "$PYHOME/" "$SIDE/python/"
    uv pip install --python "$SIDE/python/bin/python3" --target "$SIDE/site-packages" \
      -r "$ROOT/requirements-runtime.txt" -c "$ROOT/requirements.lock"
    write_launcher 'export PYTHONPATH="$DIR/site-packages:$DIR/app"
exec "$DIR/python/bin/python3" -s -m sidecar.server "$@"'
    ;;
  venv)
    PY="${PYTHON:-python3.11}"
    "$PY" -m venv --copies "$SIDE/venv"
    "$SIDE/venv/bin/pip" install -q --upgrade pip
    "$SIDE/venv/bin/pip" install -q -r "$ROOT/requirements-runtime.txt" -c "$ROOT/requirements.lock"
    write_launcher 'exec "$DIR/venv/bin/python3" -m sidecar.server "$@"'
    ;;
  pyinstaller)
    if ! command -v pyinstaller >/dev/null 2>&1; then
      echo "pyinstaller not found (pip install pyinstaller)." >&2
      exit 1
    fi
    ( cd "$ROOT" && pyinstaller --noconfirm --onedir --name aether-sidecar-bin \
        --distpath "$SIDE/dist" --workpath "$BUILD_DIR/pyi" --specpath "$BUILD_DIR/pyi" \
        --collect-submodules aether --collect-submodules sidecar --collect-all playwright \
        --add-data "$ROOT/configs:configs" --add-data "$ROOT/shared:shared" \
        --add-data "$ROOT/config.yaml:." --add-data "$ROOT/VERSION:." \
        --add-data "$ROOT/aether/knowledge/packs:aether/knowledge/packs" \
        sidecar/__main__.py >/dev/null )
    write_launcher 'exec "$DIR/dist/aether-sidecar-bin/aether-sidecar-bin" "$@"'
    ;;
  none)
    echo "    (no sidecar bundled; the app will look for a dev checkout)"
    ;;
  *)
    echo "Unknown --sidecar mode: $SIDECAR_MODE" >&2
    exit 1
    ;;
esac

echo "==> Signing"
if [[ "$IDENTITY" != "-" ]] && ! security find-identity -v -p codesigning | grep -q "\"$IDENTITY\""; then
  echo "    Signing identity '$IDENTITY' not found; falling back to ad-hoc (-)."
  echo "    macOS will forget Accessibility/Screen Recording grants on every rebuild."
  echo "    Create a stable identity once: macos/scripts/create-dev-identity.sh"
  IDENTITY="-"
fi
SIGN_OPTS=()
if $HARDENED; then SIGN_OPTS+=(--options runtime); fi
if [[ -d "$SIDE" ]]; then
  # Nested Mach-O files (the interpreter, extension modules, dylibs) first.
  find "$SIDE" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 |
    while IFS= read -r -d '' f; do
      if file -b "$f" | grep -q 'Mach-O'; then
        codesign --force --sign "$IDENTITY" ${SIGN_OPTS[@]+"${SIGN_OPTS[@]}"} "$f" >/dev/null 2>&1 \
          || echo "    warn: could not sign $f"
      fi
    done
fi
codesign --force --sign "$IDENTITY" ${SIGN_OPTS[@]+"${SIGN_OPTS[@]}"} \
  --entitlements "$AETHER_DIR/Aether.entitlements" "$APP"
codesign --verify --verbose=1 "$APP"

echo
echo "Built $APP (identity: $IDENTITY)"
echo "Open it:  open \"$APP\""
echo "First run: grant Accessibility, Screen Recording, Microphone and Input Monitoring"
echo "to Aether in System Settings → Privacy & Security, then relaunch."
