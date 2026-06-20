import AVFoundation
import Foundation
import Speech

@MainActor
final class STTBridge: ObservableObject {
    @Published var speechAuthorized = false
    @Published var partialText = ""

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var partialRequest: SFSpeechAudioBufferRecognitionRequest?
    private var partialTask: SFSpeechRecognitionTask?
    private var tapInstalled = false
    private let audioEngine = AVAudioEngine()

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

    /// Streaming partials for barge-in / HUD transcript (§6.1).
    func startPartialRecognition(onPartial: @escaping (String) -> Void) async {
        stopPartialRecognition()
        guard speechAuthorized, let recognizer, recognizer.isAvailable else { return }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        partialRequest = request

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        if !tapInstalled {
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                request.append(buffer)
            }
            tapInstalled = true
        }
        if !audioEngine.isRunning {
            try? audioEngine.start()
        }

        partialTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in
                    self.partialText = text
                    onPartial(text)
                }
            }
            if error != nil || result?.isFinal == true {
                Task { @MainActor in self.stopPartialRecognition() }
            }
        }
    }

    func stopPartialRecognition() {
        partialTask?.cancel()
        partialTask = nil
        partialRequest?.endAudio()
        partialRequest = nil
        if tapInstalled {
            audioEngine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        if audioEngine.isRunning {
            audioEngine.stop()
        }
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
