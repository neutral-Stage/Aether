import AVFoundation
import Foundation

/// OpenAI Realtime voice session via sidecar WebSocket bridge (Phase 10 beta).
@MainActor
final class RealtimeVoiceSession: NSObject, ObservableObject {
    @Published var isConnected = false
    @Published var lastTranscript = ""
    @Published var lastError: String?

    private var webSocket: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?

    var onAudioDelta: ((Data) -> Void)?
    var onTextDelta: ((String) -> Void)?

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

    func commitAudio() async {
        await sendJSON(["type": "input_audio.commit"])
    }

    private func sendJSON(_ obj: [String: Any]) async {
        guard let ws = webSocket,
              let data = try? JSONSerialization.data(withJSONObject: obj),
              let text = String(data: data, encoding: .utf8) else { return }
        do {
            try await ws.send(.string(text))
        } catch {
            lastError = error.localizedDescription
        }
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
        case "response.audio.delta":
            if let b64 = obj["delta"] as? String,
               let audio = Data(base64Encoded: b64) {
                onAudioDelta?(audio)
            }
        case "response.audio_transcript.delta",
             "conversation.item.input_audio_transcription.completed":
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
