import Foundation

/// Finds "Hey Aether …" in a transcript and returns what follows it.
enum WakeCommand {
    /// Wake phrases, plus the recognizer's usual mishearings of "Aether" that are not
    /// everyday speech ("hey either", "hey Ethan" would fire in ordinary talk).
    static let phrases = ["hey aether", "ok aether", "okay aether", "hey ether", "hey ather"]

    /// nil: no wake phrase. "": the wake phrase with nothing after it yet.
    static func command(in transcript: String) -> String? {
        let words = transcript.split(whereSeparator: { $0.isWhitespace })
        let normalized = words.map { $0.lowercased().filter { $0.isLetter } }
        var best: Int?
        for phrase in phrases {
            let parts = phrase.split(separator: " ").map(String.init)
            guard parts.count <= normalized.count else { continue }
            for start in stride(from: normalized.count - parts.count, through: 0, by: -1)
            where Array(normalized[start ..< start + parts.count]) == parts {
                let end = start + parts.count
                if best == nil || end > best! { best = end }
                break
            }
        }
        guard let end = best else { return nil }
        let rest = words[end...].joined(separator: " ")
        return rest.trimmingCharacters(in: CharacterSet.punctuationCharacters.union(.whitespaces))
    }
}

/// On-device wake word: Apple's speech recognizer with on-device recognition only,
/// so audio never leaves the Mac. "Hey Aether, open my Downloads" runs as soon as
/// the words stop changing; "Hey Aether" alone waits a few seconds for the request.
/// Recognition sessions are restarted every 55 s (the recognizer's limit is about a
/// minute) and whenever one ends early.
@MainActor
final class SpeechWakeListener {
    var onCommand: ((String) -> Void)?
    /// True while Aether should not listen (speaking, recording, running).
    var isBusy: () -> Bool = { false }

    static let stableSeconds: TimeInterval = 1.2
    static let waitForCommandSeconds: TimeInterval = 6
    static let sessionSeconds: TimeInterval = 55

    private let stt: STTBridge
    private(set) var isRunning = false
    private var sessionStarted = Date.distantPast
    private var wakeHeardAt: Date?
    private var lastChange = Date.distantPast
    private var pendingCommand = ""
    private var ticker: Task<Void, Never>?

    init(stt: STTBridge) {
        self.stt = stt
    }

    func start() {
        guard !isRunning else { return }
        isRunning = true
        ticker = Task { @MainActor [weak self] in
            while let self, self.isRunning, !Task.isCancelled {
                self.tick()
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
        }
    }

    func stop() {
        isRunning = false
        ticker?.cancel()
        ticker = nil
        endSession()
    }

    private var listening: Bool { sessionStarted != .distantPast }

    private func tick() {
        let now = Date()
        if isBusy() {
            if listening { endSession() }
            return
        }
        if !listening {
            beginSession()
            return
        }
        if let heard = wakeHeardAt {
            if !pendingCommand.isEmpty, now.timeIntervalSince(lastChange) >= Self.stableSeconds {
                let command = pendingCommand
                endSession()
                onCommand?(command)
            } else if pendingCommand.isEmpty,
                      now.timeIntervalSince(heard) >= Self.waitForCommandSeconds {
                endSession()
            }
        } else if now.timeIntervalSince(sessionStarted) >= Self.sessionSeconds {
            endSession()          // the next tick starts a fresh one
        }
    }

    private func beginSession() {
        sessionStarted = Date()
        wakeHeardAt = nil
        pendingCommand = ""
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.stt.startPartialRecognition(
                contextualStrings: ["Hey Aether", "OK Aether"],
                onPartial: { [weak self] text in self?.heard(text) },
                onEnd: { [weak self] in
                    // Ended early (error, silence limit): restart on a later tick.
                    guard let self, self.wakeHeardAt == nil else { return }
                    self.sessionStarted = .distantPast
                })
        }
    }

    private func endSession() {
        stt.stopPartialRecognition()
        sessionStarted = .distantPast
        wakeHeardAt = nil
        pendingCommand = ""
    }

    private func heard(_ text: String) {
        guard let command = WakeCommand.command(in: text) else { return }
        if wakeHeardAt == nil { wakeHeardAt = Date() }
        if command != pendingCommand {
            pendingCommand = command
            lastChange = Date()
        }
    }
}
