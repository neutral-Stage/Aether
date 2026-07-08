import Foundation

struct BetaSettings: Equatable {
    var continuousScreenStream = false
    var ambientListening = false
    var wakeWord = false
    var wakeWordEngine = "energy"
    var screenStreamFPS = 0.5
    var nativeEffectors = false
    var realtimeVoice = false
    var autoUpdateCheck = true
    var updateFeedURL = ""
    var sparkleAppcastURL = ""
    var crashReporting = false
    var pluginsEnabled = false
}

enum AetherConfig {
    static let sidecarHost = "127.0.0.1"
    static let sidecarPort = 8765
    static let nativeEffectorPort: UInt16 = 8766

    static var sidecarBaseURL: URL {
        URL(string: "http://\(sidecarHost):\(sidecarPort)")!
    }

    static let stopHotkeyModifiers: NSEvent.ModifierFlags = [.control, .shift]
    static let stopHotkeyKey: UInt16 = 1 // S

    /// Global PTT: hold Control+Space
    static let pttModifiers: NSEvent.ModifierFlags = [.control]
    static let pttKeyCode: UInt16 = 49 // Space

    /// Global command bar: ⌥Space (Phase 7)
    static let commandBarModifiers: NSEvent.ModifierFlags = [.option]
    static let commandBarKeyCode: UInt16 = 49 // Space

    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            ?? "1.0.0-rc.1"
    }

    static var autoUpdateCheckEnabled: Bool {
        if UserDefaults.standard.object(forKey: "aether.beta.auto_update_check") == nil {
            return true
        }
        return UserDefaults.standard.bool(forKey: "aether.beta.auto_update_check")
    }

    static var updateFeedURL: URL {
        let env = ProcessInfo.processInfo.environment["AETHER_UPDATE_FEED_URL"]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !env.isEmpty, let url = URL(string: env) {
            return url
        }
        if let custom = UserDefaults.standard.string(forKey: "aether.update.feed_url"),
           let url = URL(string: custom), !custom.isEmpty {
            return url
        }
        // Override repo slug via AETHER_GITHUB_REPO (default: aether-agent/aether).
        let repo = ProcessInfo.processInfo.environment["AETHER_GITHUB_REPO"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let slug = (repo?.isEmpty == false) ? repo! : "aether-agent/aether"
        return URL(string: "https://api.github.com/repos/\(slug)/releases/latest")!
    }

    /// Sparkle appcast URL — env `AETHER_SPARKLE_APPCAST_URL`, or `AETHER_UPDATE_FEED_URL` when XML.
    static var sparkleAppcastURL: URL? {
        if let dedicated = ProcessInfo.processInfo.environment["AETHER_SPARKLE_APPCAST_URL"]?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !dedicated.isEmpty,
           let url = URL(string: dedicated) {
            return url
        }
        if let custom = UserDefaults.standard.string(forKey: "aether.sparkle.appcast_url"),
           let url = URL(string: custom), !custom.isEmpty {
            return url
        }
        let feed = ProcessInfo.processInfo.environment["AETHER_UPDATE_FEED_URL"]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !feed.isEmpty, let url = URL(string: feed), Self.looksLikeAppcast(url) {
            return url
        }
        return nil
    }

    static func looksLikeAppcast(_ url: URL) -> Bool {
        let lower = url.absoluteString.lowercased()
        return lower.hasSuffix(".xml") || lower.contains("appcast") || lower.contains("sparkle")
    }

    static var hasCompletedOnboarding: Bool {
        get { UserDefaults.standard.bool(forKey: "aether.onboarding.done") }
        set { UserDefaults.standard.set(newValue, forKey: "aether.onboarding.done") }
    }

    /// Matches sidecar ``AETHER_SIDECAR_TOKEN``. Prefers the Keychain-managed
    /// token (auth-on-by-default, Phase 7); falls back to an env override.
    static var sidecarBearerToken: String? {
        if let token = KeyStore.sidecarToken(), !token.isEmpty { return token }
        let raw = ProcessInfo.processInfo.environment["AETHER_SIDECAR_TOKEN"]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return raw.isEmpty ? nil : raw
    }

    /// Native effector auth — falls back to sidecar token when unset.
    static var nativeEffectorToken: String? {
        let dedicated = ProcessInfo.processInfo.environment["AETHER_NATIVE_EFFECTOR_TOKEN"]?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !dedicated.isEmpty { return dedicated }
        return sidecarBearerToken
    }
}

import AppKit
