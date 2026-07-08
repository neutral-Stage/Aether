# Code signing & notarization (Phase 3/5)

Aether requires entitlements that are **not App Store compatible** (accessibility, screen capture, input monitoring). Ship as a **Developer ID-signed, notarized** `.app` outside the Mac App Store.

## Prerequisites

- Apple Developer account with **Developer ID Application** certificate
- Xcode 15+ and `xcode-select` pointed at it
- App-specific password for notarytool (stored in keychain)

## Entitlements

See `macos/Aether/Aether.entitlements`. Typical entries:

- `com.apple.security.app-sandbox` — **false** (or omit sandbox for automation apps)
- Hardened Runtime enabled
- `com.apple.security.device.audio-input` — microphone
- `com.apple.security.device.camera` — optional camera (Phase 2+)

Review with:

```bash
codesign -d --entitlements - macos/Aether/.build/release/Aether.app
```

## Build release binary

```bash
cd macos/Aether
swift build -c release
```

Package as `.app` bundle (copy `Info.plist`, resources, embed sidecar launch script if needed).

## Sign

```bash
APP="build/Aether.app"
codesign --force --deep --options runtime \
  --entitlements Aether.entitlements \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  "$APP"
codesign --verify --verbose=2 "$APP"
```

## Notarize & staple

```bash
ditto -c -k --keepParent "$APP" Aether.zip
xcrun notarytool submit Aether.zip \
  --apple-id "you@example.com" \
  --team-id TEAMID \
  --password "@keychain:AC_PASSWORD" \
  --wait
xcrun stapler staple "$APP"
```

## Export for CI

`ExportOptions.plist` is provided for `xcodebuild -exportArchive` workflows.

## Distribution notes

- First launch: users must approve **Accessibility**, **Screen Recording**, **Microphone**, and **Input Monitoring** in System Settings.
- Document permissions in `BETA.md` and onboarding UI.
- Do not ship `data/.audit_hmac_key` or `.env` in the bundle.

## Sparkle (optional)

For production auto-update, integrate [Sparkle 2](https://sparkle-project.org/) SPM dependency and set
`beta.sparkle_appcast_url` in `config.yaml`. Phase 9 ships `SparkleUpdateController.swift` (appcast XML
parser stub) until full Sparkle is wired — see `UpdateChecker.swift` GitHub fallback.

## Phase 7 — self-contained packaging

The app now **starts and supervises its own sidecar** (`SidecarSupervisor.swift`),
reads API keys from the **Keychain** (Settings → API Keys, `KeyStore.swift`), and
**auth is on by default** (a per-install token is generated on first run and
injected into the sidecar). A shipped user needs no terminal, no `.env`, and no
Python checkout — *if* the sidecar is bundled.

**Build a distributable DMG:**
```bash
pip install pyinstaller          # once; needed to freeze the sidecar
macos/scripts/build-dmg.sh       # stamps VERSION, builds app, freezes+bundles sidecar, makes DMG
macos/scripts/sign-and-notarize.sh macos/build/Aether.app   # your Developer ID
```
`build-dmg.sh` runs `stamp-version.sh` so `Info.plist` always matches `VERSION`
(no more drift), and PyInstaller-freezes the sidecar into
`Aether.app/Contents/Resources/sidecar/aether-sidecar`. The supervisor prefers
that binary and falls back to a dev checkout's `python3 -m sidecar.server` when
it's absent (so the `swift build` dev flow is unchanged).

**Requires your machine / Apple account (cannot be done in CI without secrets):**
- A **Developer ID Application** certificate + a notarytool app-specific password.
- Validate the frozen sidecar launches on a clean machine — PyInstaller bundling
  of pyobjc/uvicorn is environment-sensitive; test the DMG on a second Mac before
  distributing.
- Keep Hardened Runtime + the automation entitlements (see above).
