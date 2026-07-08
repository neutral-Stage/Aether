import AppKit
import Foundation

enum SidecarEvent {
    case runStart(runId: String, goal: String)
    case hud([String: Any])
    case say(String)
    case done(result: String, world: WorldSnapshot?)
    case error(String)
    case stopped
    case ping
    case confirmRequest(requestId: String, description: String)
    case fleet([String: Any])
}

struct RunOptions {
    var careful: Bool = false
    var localOnly: Bool = false
    var stream: Bool = true
}

@MainActor
final class OrchestratorClient: ObservableObject {
    @Published var isRunning = false
    @Published var lastError: String?
    @Published var healthOK = false

    private var streamTask: Task<Void, Never>?

    private func applySidecarAuth(_ request: inout URLRequest) {
        if let token = AetherConfig.sidecarBearerToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    func checkHealth() async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("health")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                healthOK = false
                return
            }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               json["ok"] as? Bool == true {
                healthOK = true
            } else {
                healthOK = false
            }
        } catch {
            healthOK = false
        }
    }

    func fetchVoiceConfig() async -> VoiceSettings? {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("config/voice")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return nil }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            return VoiceSettings(
                stt: json["stt"] as? String ?? "groq",
                sttModel: json["stt_model"] as? String ?? "whisper-large-v3-turbo",
                tts: json["tts"] as? String ?? "groq",
                ttsModel: json["tts_model"] as? String ?? "canopylabs/orpheus-v1-english",
                ttsVoice: json["tts_voice"] as? String ?? "troy",
                ttsStream: json["tts_stream"] as? Bool ?? true,
                mode: json["mode"] as? String ?? "pipeline",
                realtimeProvider: json["realtime_provider"] as? String ?? "openai",
                realtimeVoice: json["realtime_voice"] as? Bool ?? false,
                bargeIn: json["barge_in"] as? Bool ?? true,
                vadEnergyThreshold: (json["vad_energy_threshold"] as? NSNumber)?.floatValue ?? 0.02
            )
        } catch {
            return nil
        }
    }

    func fetchBetaConfig() async -> BetaSettings? {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("config/beta")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return nil }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            var beta = BetaSettings()
            beta.continuousScreenStream = json["continuous_screen_stream"] as? Bool ?? false
            beta.ambientListening = json["ambient_listening"] as? Bool ?? false
            beta.wakeWord = json["wake_word"] as? Bool ?? false
            beta.wakeWordEngine = json["wake_word_engine"] as? String ?? "energy"
            beta.screenStreamFPS = (json["screen_stream_fps"] as? NSNumber)?.doubleValue ?? 0.5
            beta.nativeEffectors = json["native_effectors"] as? Bool ?? false
            beta.realtimeVoice = json["realtime_voice"] as? Bool ?? false
            beta.autoUpdateCheck = json["auto_update_check"] as? Bool ?? true
            beta.updateFeedURL = json["update_feed_url"] as? String ?? ""
            beta.sparkleAppcastURL = json["sparkle_appcast_url"] as? String ?? ""
            beta.crashReporting = json["crash_reporting"] as? Bool ?? false
            beta.pluginsEnabled = json["plugins_enabled"] as? Bool ?? false
            if !beta.updateFeedURL.isEmpty {
                UserDefaults.standard.set(beta.updateFeedURL, forKey: "aether.update.feed_url")
            }
            if !beta.sparkleAppcastURL.isEmpty {
                UserDefaults.standard.set(beta.sparkleAppcastURL, forKey: "aether.sparkle.appcast_url")
            }
            UserDefaults.standard.set(beta.autoUpdateCheck, forKey: "aether.beta.auto_update_check")
            return beta
        } catch {
            return nil
        }
    }

    func submitConfirmation(requestId: String, approved: Bool) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("confirm")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["request_id": requestId, "approved": approved]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func reportVoiceMetrics(sttMs: Double? = nil, ttsMs: Double? = nil, voiceRttMs: Double? = nil) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("metrics/voice")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = [:]
        if let sttMs { body["stt_ms"] = sttMs }
        if let ttsMs { body["tts_ms"] = ttsMs }
        if let voiceRttMs { body["voice_rtt_ms"] = voiceRttMs }
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func postScreenPercept(width: Int, height: Int, fps: Double, note: String) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("percept/screen")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "width": width,
            "height": height,
            "fps": fps,
            "frontmost_app": NSWorkspace.shared.frontmostApplication?.localizedName ?? "",
            "note": note,
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func submitFeedback(message: String, category: String = "general") async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("feedback")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "message": message,
            "category": category,
            "app_version": AetherConfig.appVersion,
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func fetchMCPConfig() async -> MCPConfig? {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("config/mcp")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return nil }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let serversRaw = json["servers"] as? [[String: Any]] ?? []
            let servers = serversRaw.map { entry -> MCPServerStatus in
                MCPServerStatus(
                    name: entry["name"] as? String ?? "unnamed",
                    enabled: entry["enabled"] as? Bool ?? false,
                    transport: entry["transport"] as? String ?? "stdio",
                    status: entry["status"] as? String ?? "unknown",
                    url: entry["url"] as? String,
                    command: entry["command"] as? String
                )
            }
            return MCPConfig(
                enabled: json["enabled"] as? Bool ?? false,
                servers: servers,
                reloadNote: json["reload_note"] as? String ?? ""
            )
        } catch {
            return nil
        }
    }

    func fetchSkills() async throws -> [SkillSummary] {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("skills")
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return []
        }
        let raw = json["skills"] as? [[String: Any]] ?? []
        return raw.compactMap { SkillSummary(dict: $0) }
    }

    func fetchFleet() async throws -> [FleetSession] {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("fleet")
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return []
        }
        let raw = json["sessions"] as? [[String: Any]] ?? []
        return raw.compactMap { FleetSession(dict: $0) }
    }

    private func fleetPost(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        let url = AetherConfig.sidecarBaseURL
            .appendingPathComponent("fleet")
            .appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        applySidecarAuth(&request)
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func sendToAgent(id: String, text: String) async throws -> Bool {
        let json = try await fleetPost("\(id)/send", body: ["text": text])
        return json["delivered"] as? Bool ?? false
    }

    func stopAgent(id: String) async throws {
        _ = try await fleetPost("\(id)/stop")
    }

    func stopAllAgents() async throws -> Int {
        let json = try await fleetPost("stop_all")
        return json["stopped"] as? Int ?? 0
    }

    func replaySkill(id: Int, args: [String: String], viaOrchestrator: Bool) async throws -> String {
        let url = AetherConfig.sidecarBaseURL
            .appendingPathComponent("skills")
            .appendingPathComponent("\(id)")
            .appendingPathComponent("replay")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "args": args,
            "via_orchestrator": viaOrchestrator,
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "Replay failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: msg])
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return "Replay complete"
        }
        if viaOrchestrator {
            return json["result"] as? String ?? json["status"] as? String ?? "Replay started"
        }
        let ok = json["success"] as? Bool ?? false
        let steps = json["steps_executed"] as? Int ?? 0
        if ok {
            return "Direct replay OK (\(steps) steps)"
        }
        return json["error"] as? String ?? "Replay failed"
    }

    func reloadMCP() async -> String {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("config/mcp/reload")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        applySidecarAuth(&request)
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
                return "Reload failed"
            }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let status = json["status"] as? String {
                return "MCP \(status)"
            }
            return "MCP reloaded"
        } catch {
            return error.localizedDescription
        }
    }

    func synthesize(text: String) async throws -> Data {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("tts")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["text": text]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "TTS failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: msg])
        }
        return data
    }

    /// Streaming TTS via `POST /tts/stream` — accumulates chunks (Phase 10).
    func synthesizeStream(text: String) async throws -> Data {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("tts/stream")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["text": text]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
            var data = Data()
            for try await byte in bytes { data.append(byte) }
            let msg = String(data: data, encoding: .utf8) ?? "TTS stream failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: msg])
        }
        var data = Data()
        for try await byte in bytes {
            data.append(byte)
        }
        return data
    }

    struct Catalog: Decodable {
        struct App: Decodable, Identifiable { let key: String; let app: String; var id: String { key } }
        struct Tool: Decodable, Identifiable { let name: String; let description: String; let impact: String; var id: String { name } }
        let apps: [App]
        let tools: [Tool]
        let tool_count: Int
    }

    struct TaskGraph: Decodable, Identifiable {
        struct Node: Decodable, Identifiable {
            let id: String
            let title: String
            let status: String
            let depends_on: [String]
        }
        let graph_id: String
        let goal: String
        let status: String
        let integration_branch: String?
        let nodes: [Node]
        var id: String { graph_id }
    }

    /// Live task graphs (Phase 8 orchestration).
    func fetchGraphs() async -> [TaskGraph] {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("fleet/graphs")
        var request = URLRequest(url: url)
        applySidecarAuth(&request)
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse, http.statusCode == 200,
              let decoded = try? JSONDecoder().decode([String: [TaskGraph]].self, from: data) else {
            return []
        }
        return decoded["graphs"] ?? []
    }

    /// What Aether can do — supported apps + tools (Phase 7 discoverability).
    func fetchCatalog() async -> Catalog? {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("catalog")
        var request = URLRequest(url: url)
        applySidecarAuth(&request)
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            return nil
        }
        return try? JSONDecoder().decode(Catalog.self, from: data)
    }

    func stop() async -> Double {
        let started = Date()
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("stop")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
        let latencyMs = Date().timeIntervalSince(started) * 1000
        await reportStopMetrics(stopLatencyMs: latencyMs)
        return latencyMs
    }

    func reportStopMetrics(stopLatencyMs: Double) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("metrics/stop")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "stop_latency_ms": stopLatencyMs,
            "source": "swift",
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func run(
        goal: String,
        options: RunOptions = RunOptions(),
        onEvent: @escaping (SidecarEvent) -> Void
    ) {
        streamTask?.cancel()
        isRunning = true
        lastError = nil

        streamTask = Task {
            defer {
                Task { @MainActor in
                    self.isRunning = false
                }
            }
            do {
                try await self.streamRun(goal: goal, options: options, onEvent: onEvent)
            } catch {
                await MainActor.run {
                    self.lastError = error.localizedDescription
                    onEvent(.error(error.localizedDescription))
                }
            }
        }
    }

    func cancelRun() {
        streamTask?.cancel()
        streamTask = nil
        isRunning = false
        Task { await stop() }
    }

    private func streamRun(
        goal: String,
        options: RunOptions,
        onEvent: @escaping (SidecarEvent) -> Void
    ) async throws {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("run")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        let body: [String: Any] = [
            "goal": goal,
            "careful": options.careful,
            "local_only": options.localOnly,
            "stream": options.stream,
            "narrate": false,
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)

        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if http.statusCode == 409 {
            throw NSError(domain: "Aether", code: 409, userInfo: [
                NSLocalizedDescriptionKey: "Agent is already running another task.",
            ])
        }
        guard (200 ... 299).contains(http.statusCode) else {
            var data = Data()
            for try await byte in bytes { data.append(byte) }
            let msg = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw NSError(domain: "Aether", code: http.statusCode, userInfo: [
                NSLocalizedDescriptionKey: msg,
            ])
        }

        var lineBuffer = ""
        for try await byte in bytes {
            try Task.checkCancellation()
            let ch = Character(UnicodeScalar(byte))
            if ch == "\n" {
                let line = lineBuffer.trimmingCharacters(in: .whitespacesAndNewlines)
                lineBuffer = ""
                guard line.hasPrefix("data:") else { continue }
                let jsonStr = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                guard let data = jsonStr.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let type = obj["type"] as? String else { continue }

                await MainActor.run {
                    switch type {
                    case "run_start":
                        let runId = obj["run_id"] as? String ?? ""
                        let g = obj["goal"] as? String ?? goal
                        onEvent(.runStart(runId: runId, goal: g))
                    case "hud":
                        onEvent(.hud(obj))
                    case "say":
                        if let text = obj["text"] as? String { onEvent(.say(text)) }
                    case "fleet":
                        onEvent(.fleet(obj))
                    case "done":
                        let result = obj["result"] as? String ?? "Done."
                        var world: WorldSnapshot?
                        if let w = obj["world"] as? [String: Any],
                           let wData = try? JSONSerialization.data(withJSONObject: w) {
                            world = try? JSONDecoder().decode(WorldSnapshot.self, from: wData)
                        }
                        onEvent(.done(result: result, world: world))
                    case "error":
                        let msg = obj["message"] as? String ?? "Unknown error"
                        onEvent(.error(msg))
                    case "stopped":
                        onEvent(.stopped)
                    case "ping":
                        onEvent(.ping)
                    case "confirm_request":
                        let rid = obj["request_id"] as? String ?? ""
                        let desc = obj["description"] as? String ?? "Proceed?"
                        onEvent(.confirmRequest(requestId: rid, description: desc))
                    default:
                        break
                    }
                }
            } else {
                lineBuffer.append(ch)
            }
        }
    }

    func transcribe(wavData: Data) async throws -> String {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("stt")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "audio_base64": wavData.base64EncodedString(),
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "STT failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: msg])
        }
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}
