import Foundation

/// Decides when a partial transcript (from `STTBridge.makePartialRecognizer()`,
/// running while push-to-talk is held) is worth firing talk mode's model call on
/// speculatively, before the user lets go of the keys.
///
/// A pure value type: `AppState` feeds it partials as they arrive and polls
/// `fire(at:)` on a short timer while the talk chord is held.
struct SpeculationPolicy {
    private let stableSeconds: TimeInterval
    private let minWords: Int
    private let maxFires: Int

    /// Latest partial, as heard (not normalized) — what `fire()` returns.
    private var rawText = ""
    /// Normalized form of `rawText`, used to tell whether it actually changed.
    private var normalizedText = ""
    /// When `normalizedText` last changed; nil once nothing has been observed.
    private var stableSince: TimeInterval?
    private var firesUsed = 0
    /// Normalized form of the last text `fire()` returned, so the same settled
    /// text isn't fired twice in a row.
    private var lastFiredNormalized: String?

    init(stableSeconds: TimeInterval = 1.2, minWords: Int = 4, maxFires: Int = 2) {
        self.stableSeconds = stableSeconds
        self.minWords = minWords
        self.maxFires = maxFires
    }

    /// Records a new partial transcript. The stability clock resets whenever the
    /// *normalized* text changes (so punctuation/case flicker from the recognizer
    /// doesn't itself count as a change).
    mutating func observe(_ partial: String, at time: TimeInterval) {
        rawText = partial
        let normalized = Self.normalize(partial)
        guard normalized != normalizedText else { return }
        normalizedText = normalized
        stableSince = time
    }

    /// Returns the current partial once it has held still for `stableSeconds`, has
    /// at least `minWords` words, fewer than `maxFires` fires have happened yet,
    /// and it differs (normalized) from whatever was last fired. Counts the fire.
    mutating func fire(at time: TimeInterval) -> String? {
        guard firesUsed < maxFires,
              let since = stableSince, time - since >= stableSeconds,
              normalizedText.split(separator: " ").count >= minWords,
              normalizedText != lastFiredNormalized
        else { return nil }
        firesUsed += 1
        lastFiredNormalized = normalizedText
        return rawText
    }

    /// Lowercases, drops punctuation and symbols (hyphens included — "Wi-Fi" and
    /// "wifi" normalize the same way, since a dropped hyphen just glues its two
    /// sides together), and collapses whitespace runs to single spaces.
    static func normalize(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for scalar in s.lowercased().unicodeScalars {
            if CharacterSet.alphanumerics.contains(scalar) {
                out.unicodeScalars.append(scalar)
            } else if CharacterSet.whitespacesAndNewlines.contains(scalar) {
                out.append(" ")
            }
        }
        return out.split(separator: " ").joined(separator: " ")
    }

    /// Whether two strings are the same question once normalized.
    static func matches(_ a: String, _ b: String) -> Bool {
        normalize(a) == normalize(b)
    }
}
