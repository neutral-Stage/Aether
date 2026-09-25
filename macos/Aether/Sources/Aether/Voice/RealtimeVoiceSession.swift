import AVFoundation
import Foundation

/// OpenAI Realtime voice session via sidecar WebSocket bridge (Phase 10 beta).
@MainActor
final class RealtimeVoiceSession: NSObject, ObservableObject {
    @Published var isConnected = false
    /// The reply's transcript so far.
    @Published var lastTranscript = ""
    /// The user's last spoken turn, as the realtime model heard it.
    @Published var lastUserTranscript = ""
    @Published var lastError: String?

    private var webSocket: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?

    /// Reply audio (24 kHz PCM16) and the id of the reply item it belongs to.
    var onAudioDelta: ((Data, String?) -> Void)?
    var onTextDelta: ((String) -> Void)?
    /// A reply finished (its transcript), for the HUD.
    var onReplyDone: ((String) -> Void)?
    private var sendChain: Task<Void, Never>?

    func connect() async {
        guard !isConnected else { return }
        let wsURL = AetherConfig.sidecarBaseURL
            .appendingPathComponent("voice/realtime")
        var components = URLComponents(url: wsURL, resolvingAgainstBaseURL: false)!
        components.scheme = "ws"
        guard let url = components.url else {
            lastError = "Invalid realtime WebSocket URL"
            return
        }

        var request = URLRequest(url: url)
        if let token = AetherConfig.sidecarBearerToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let session = URLSession(configuration: .default)
        let task = session.webSocketTask(with: request)
        webSocket = task
        task.resume()
        isConnected = true
        receiveTask = Task { await receiveLoop() }
    }

    func disconnect() {
        receiveTask?.cancel()
        receiveTask = nil
        webSocket?.cancel(with: .goingAway, reason: nil)
        webSocket = nil
        isConnected = false
    }

    func sendText(_ text: String) async {
        let payload: [String: Any] = ["type": "input_text", "text": text]
        await sendJSON(payload)
    }

    func sendAudio(pcm16: Data) async {
        let payload: [String: Any] = [
            "type": "input_audio",
            "audio": pcm16.base64EncodedString(),
        ]
        await sendJSON(payload)
    }

    /// End of the user's turn (push-to-talk released).
    func commitAudio() async {
        await sendJSON(["type": "input_audio.commit"])
    }

    /// Start of a new turn: drop any half-sent audio.
    func clearInput() async {
        await sendJSON(["type": "input_audio.clear"])
    }

    /// The user talked over the reply: stop it where they stopped hearing it.
    func interrupt(itemId: String?, audioEndMs: Int) async {
        var msg: [String: Any] = ["type": "interrupt", "audio_end_ms": audioEndMs]
        if let itemId { msg["item_id"] = itemId }
        await sendJSON(msg)
    }

    /// Sends go out in the order they were made (audio chunks, then the commit).
    private func sendJSON(_ obj: [String: Any]) async {
        guard let data = try? JSONSerialization.data(withJSONObject: obj),
              let text = String(data: data, encoding: .utf8) else { return }
        let previous = sendChain
        let task = Task { @MainActor [weak self] in
            await previous?.value
            guard let self, let ws = self.webSocket else { return }
            do {
                try await ws.send(.string(text))
            } catch {
                self.lastError = error.localizedDescription
            }
        }
        sendChain = task
        await task.value
    }

    private func receiveLoop() async {
        guard let ws = webSocket else { return }
        while !Task.isCancelled {
            do {
                let message = try await ws.receive()
                switch message {
                case .string(let text):
                    handleEvent(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        handleEvent(text)
                    }
                @unknown default:
                    break
                }
            } catch {
                if !Task.isCancelled {
                    lastError = error.localizedDescription
                    isConnected = false
                }
                break
            }
        }
    }

    private func handleEvent(_ jsonText: String) {
        guard let data = jsonText.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = obj["type"] as? String else { return }

        switch type {
        case "session.ready":
            break
        case "response.output_audio.delta", "response.audio.delta":
            if let b64 = obj["delta"] as? String,
               let audio = Data(base64Encoded: b64) {
                onAudioDelta?(audio, obj["item_id"] as? String)
            }
        case "response.done":
            onReplyDone?(lastTranscript)
            lastTranscript = ""
        case "conversation.item.input_audio_transcription.completed":
            // What the user said, not the reply: kept apart so it never shows
            // up in (or replaces) the reply's transcript.
            if let transcript = obj["transcript"] as? String {
                lastUserTranscript = transcript
            }
        case "response.output_audio_transcript.delta", "response.audio_transcript.delta":
            if let delta = obj["delta"] as? String {
                lastTranscript += delta
                onTextDelta?(delta)
            } else if let transcript = obj["transcript"] as? String {
                lastTranscript = transcript
                onTextDelta?(transcript)
            }
        case "error":
            lastError = obj["message"] as? String ?? "Realtime error"
        default:
            break
        }
    }
}
