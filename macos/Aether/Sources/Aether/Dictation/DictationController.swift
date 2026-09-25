import AppKit
import Foundation

/// ⌃⌥D: dictate into whatever text field has focus. Press to start, press again to
/// finish; the words are transcribed with your vocabulary, cleaned up in the tone
/// that suits the app, and pasted. Never types into password fields.
@MainActor
final class DictationController {
    enum State: Equatable { case idle, recording, working }

    private(set) var state = State.idle
    var onStatus: ((String) -> Void)?

    private let audio: AudioEngine
    private let client: OrchestratorClient
    private var target: (name: String, bundleId: String, app: NSRunningApplication?)?

    init(audio: AudioEngine, client: OrchestratorClient) {
        self.audio = audio
        self.client = client
    }

    func toggle() async {
        switch state {
        case .idle: start()
        case .recording: await finish()
        case .working: break
        }
    }

    func cancel() {
        guard state == .recording else { return }
        _ = audio.stopRecording()
        state = .idle
        onStatus?("")
    }

    private func start() {
        guard !TextInsertion.secureInputActive() else {
            onStatus?("Dictation is off in password fields.")
            return
        }
        target = TextInsertion.frontmostApp()
        do {
            try audio.startRecording()
        } catch {
            onStatus?("Can't use the microphone: \(error.localizedDescription)")
            return
        }
        state = .recording
        onStatus?("Dictating into \(target?.name ?? "this app")… ⌃⌥D to finish")
    }

    private func finish() async {
        let wav = audio.stopRecording()
        state = .working
        defer { state = .idle }
        guard !wav.isEmpty else {
            onStatus?("")
            return
        }
        onStatus?("Writing…")
        do {
            let raw = try await client.transcribe(wavData: wav, useVocabulary: true)
            guard !raw.isEmpty else {
                onStatus?("Didn't catch that.")
                return
            }
            let text = await client.cleanDictation(raw, bundleId: target?.bundleId ?? "",
                                                   app: target?.name ?? "") ?? raw
            // Back to the app we were dictating into, then paste.
            if let app = target?.app, app != NSWorkspace.shared.frontmostApplication {
                _ = app.activate()
                try? await Task.sleep(nanoseconds: 150_000_000)
            }
            guard !TextInsertion.secureInputActive() else {
                onStatus?("Not typing into a password field.")
                return
            }
            await TextInsertion.paste(text)
            onStatus?("")
        } catch {
            onStatus?(error.localizedDescription)
        }
    }
}
