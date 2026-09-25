import AVFoundation
import Foundation
import Speech

/// One on-device speech-recognition session fed from the shared microphone hub.
/// Independent of any other session — barge-in and the wake listener each run
/// their own `PartialRecognizer` and no longer cancel one another.
@MainActor
final class PartialRecognizer {
    private(set) var isRunning = false

    private let recognizer: SFSpeechRecognizer?
    private let hub: MicHub
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var subscription: MicSubscription?

    init(recognizer: SFSpeechRecognizer?, hub: MicHub = .shared) {
        self.recognizer = recognizer
        self.hub = hub
    }

    /// Streaming partials for barge-in / HUD transcript / wake word (§6.1).
    /// `contextualStrings` bias recognition toward phrases (the wake word); `onEnd`
    /// runs when the recognizer stops by itself (error, final result, time limit).
    /// Returns whether a session actually started.
    @discardableResult
    func start(contextualStrings: [String] = [],
              onPartial: @escaping (String) -> Void,
              onEnd: (() -> Void)? = nil) async -> Bool {
        stop()
        guard SFSpeechRecognizer.authorizationStatus() == .authorized,
              let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition
        else {
            onEnd?()
            return false
        }

        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = true
        req.contextualStrings = contextualStrings

        do {
            subscription = try hub.subscribe { buffer in
                req.append(buffer)
            }
        } catch {
            onEnd?()
            return false
        }
        request = req
        isRunning = true

        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in
                    onPartial(text)
                }
            }
            if error != nil || result?.isFinal == true {
                Task { @MainActor in
                    // Only if this is still the current session (not a newer one).
                    guard self.request === req else { return }
                    self.stop()
                    onEnd?()
                }
            }
        }
        return true
    }

    func stop() {
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
        subscription?.cancel()
        subscription = nil
        isRunning = false
    }
}

@MainActor
final class STTBridge: ObservableObject {
    @Published var speechAuthorized = false
    @Published var partialText = ""

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private lazy var defaultRecognizer = PartialRecognizer(recognizer: recognizer)

    func refreshAuthorization() {
        speechAuthorized = SFSpeechRecognizer.authorizationStatus() == .authorized
    }

    func requestAuthorization() async -> Bool {
        await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                Task { @MainActor in
                    self.speechAuthorized = status == .authorized
                    cont.resume(returning: status == .authorized)
                }
            }
        }
    }

    /// An independent partial-recognition session, fed from the shared mic hub —
    /// for a feature (like barge-in) that must not cancel `startPartialRecognition`'s
    /// default session, or be cancelled by it.
    func makePartialRecognizer() -> PartialRecognizer {
        PartialRecognizer(recognizer: recognizer)
    }

    /// Streaming partials on the default session (see `PartialRecognizer.start`).
    func startPartialRecognition(contextualStrings: [String] = [],
                                 onPartial: @escaping (String) -> Void,
                                 onEnd: (() -> Void)? = nil) async {
        partialText = ""
        await defaultRecognizer.start(
            contextualStrings: contextualStrings,
            onPartial: { [weak self] text in
                self?.partialText = text
                onPartial(text)
            },
            onEnd: { [weak self] in
                self?.partialText = ""
                onEnd?()
            }
        )
    }

    func stopPartialRecognition() {
        defaultRecognizer.stop()
        partialText = ""
    }

    /// Apple Speech on-device path for low-latency PTT (preferred over sidecar round-trip).
    func transcribe(wavData: Data) async throws -> String {
        guard speechAuthorized, let recognizer, recognizer.isAvailable else {
            throw NSError(domain: "Aether", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Speech recognition not authorized or unavailable.",
            ])
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("aether-ptt-\(UUID().uuidString).wav")
        try wavData.write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let request = SFSpeechURLRecognitionRequest(url: url)
        request.shouldReportPartialResults = false
        request.requiresOnDeviceRecognition = true

        return try await withCheckedThrowingContinuation { cont in
            recognizer.recognitionTask(with: request) { result, error in
                if let error {
                    cont.resume(throwing: error)
                    return
                }
                guard let result, result.isFinal else { return }
                cont.resume(returning: result.bestTranscription.formattedString)
            }
        }
    }
}
