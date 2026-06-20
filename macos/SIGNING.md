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
