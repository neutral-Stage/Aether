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
    case runRequest(goal: String)   // proactive trigger auto-run (Phase 11)
    case question(requestId: String, question: String, options: [String])  // ask_user
    case session(String)            // conversation id for follow-ups
    case step([String: Any])        // tool_call / tool_result / screenshot / text / plan
    case pointer([OverlayTarget])   // show the user where something is
    case guideStep(id: String, index: Int, total: Int, say: String, target: OverlayTarget?)
    case guideDone(id: String, status: String)
    case token(step: Int, text: String)           // an agent run's reply as it streams
    case talkToken(id: String, text: String)      // a talk answer as it streams (tags removed)
    case talkDone(id: String, answer: String)

    /// Events carried by one SSE `data:` object from POST /run (unknown types → none).
    static func parse(_ obj: [String: Any], fallbackGoal: String) -> [SidecarEvent] {
        guard let type = obj["type"] as? String else { return [] }
        var out: [SidecarEvent] = []
        switch type {
        case "run_start":
            if let sid = obj["session_id"] as? String { out.append(.session(sid)) }
            out.append(.runStart(runId: obj["run_id"] as? String ?? "",
                                 goal: obj["goal"] as? String ?? fallbackGoal))
        case "hud":
            out.append(.hud(obj))
        case "say":
            if let text = obj["text"] as? String { out.append(.say(text)) }
        case "fleet":
            out.append(.fleet(obj))
        case "run_request":
            if let g = obj["goal"] as? String { out.append(.runRequest(goal: g)) }
        case "done":
            if let sid = obj["session_id"] as? String { out.append(.session(sid)) }
            var world: WorldSnapshot?
            if let w = obj["world"] as? [String: Any],
               let wData = try? JSONSerialization.data(withJSONObject: w) {
                world = try? JSONDecoder().decode(WorldSnapshot.self, from: wData)
            }
            out.append(.done(result: obj["result"] as? String ?? "Done.", world: world))
        case "error":
            out.append(.error(obj["message"] as? String ?? "Unknown error"))
        case "stopped":
            out.append(.stopped)
        case "ping":
            out.append(.ping)
        case "confirm_request":
            out.append(.confirmRequest(requestId: obj["request_id"] as? String ?? "",
                                       description: obj["description"] as? String ?? "Proceed?"))
        case "question":
            // The agent's own copy of the event has no request id and cannot be
            // answered; the sidecar's copy does.
            let rid = obj["request_id"] as? String ?? ""
            let q = obj["question"] as? String ?? ""
            if !rid.isEmpty, !q.isEmpty {
                out.append(.question(requestId: rid, question: q,
                                     options: obj["options"] as? [String] ?? []))
            }
        case "tool_call", "tool_result", "screenshot", "text", "plan":
            out.append(.step(obj))
        case "pointer":
            let targets = (obj["targets"] as? [[String: Any]] ?? []).compactMap(OverlayTarget.init(json:))
            if !targets.isEmpty { out.append(.pointer(targets)) }
        case "guide_step":
            guard let id = obj["guide_id"] as? String, let say = obj["say"] as? String else { break }
            let target = (obj["target"] as? [String: Any]).flatMap(OverlayTarget.init(json:))
            out.append(.guideStep(id: id, index: (obj["index"] as? NSNumber)?.intValue ?? 0,
                                  total: (obj["total"] as? NSNumber)?.intValue ?? 0,
                                  say: say, target: target))
        case "token":
            if let text = obj["text"] as? String {
                out.append(.token(step: obj["step"] as? Int ?? 0, text: text))
            }
        case "talk_token":
            if let id = obj["talk_id"] as? String, let text = obj["text"] as? String {
                out.append(.talkToken(id: id, text: text))
            }
        case "talk_done":
            if let id = obj["talk_id"] as? String {
                out.append(.talkDone(id: id, answer: obj["answer"] as? String ?? ""))
            }
        case "guide_done":
            if let id = obj["guide_id"] as? String {
                out.append(.guideDone(id: id, status: obj["status"] as? String ?? "done"))
            }
        default:
            break
        }
        return out
    }
}

struct DoctorCheck: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let status: String   // ok | warn | fail
    let detail: String
    let fix: String
}

struct DoctorReport: Equatable {
    let verdict: String
    let checks: [DoctorCheck]

    static func parse(_ data: Data) -> DoctorReport? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let verdict = json["verdict"] as? String else { return nil }
        let checks = (json["checks"] as? [[String: Any]] ?? []).compactMap { raw -> DoctorCheck? in
            guard let name = raw["name"] as? String, let status = raw["status"] as? String else {
                return nil
            }
            return DoctorCheck(name: name, status: status,
                               detail: raw["detail"] as? String ?? "",
                               fix: raw["fix"] as? String ?? "")
        }
        return DoctorReport(verdict: verdict, checks: checks)
    }
}

/// "Show me how to …" requests go to guide mode; spoken controls steer it.
enum GuideIntent {
    private static let prefixes = ["show me how", "teach me", "walk me through", "guide me",
                                   "how do i "]

    static func isGuideRequest(_ text: String) -> Bool {
        var t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        for lead in ["hey aether, ", "hey aether ", "ok aether, ", "please "] where t.hasPrefix(lead) {
            t = String(t.dropFirst(lead.count))
        }
        return prefixes.contains { t.hasPrefix($0) }
    }

    /// The guide control a spoken phrase asks for, if any.
    static func control(for text: String) -> String? {
        let t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
        if t.contains("do it for me") || t == "do it" || t.contains("you do it") { return "do_it" }
        if ["stop", "cancel", "quit", "stop the guide", "never mind"].contains(t) { return "stop" }
        if ["back", "go back", "previous", "previous step"].contains(t) { return "back" }
        if ["repeat", "again", "say that again", "repeat that"].contains(t) { return "repeat" }
        if ["skip", "skip it", "skip this"].contains(t) { return "skip" }
        if ["next", "done", "next step", "ok", "okay", "got it"].contains(t) { return "next" }
        return nil
    }
}

/// POST /talk's reply: what to say and where to point.
struct TalkReply {
    let answer: String
    let targets: [OverlayTarget]
    let sessionId: String?

    static func parse(_ data: Data) -> TalkReply? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let answer = json["answer"] as? String else { return nil }
        let targets = (json["targets"] as? [[String: Any]] ?? []).compactMap(OverlayTarget.init(json:))
        return TalkReply(answer: answer, targets: targets, sessionId: json["session_id"] as? String)
    }
}

struct RunOptions {
    var careful: Bool = false
    var localOnly: Bool = false
    var stream: Bool = true
    /// Continue this conversation (nil starts a new one).
    var sessionId: String? = nil
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

    /// Preflight checks from the sidecar (`GET /doctor`). `online` also tests
    /// the default brain's API key against the provider.
    func fetchDoctor(online: Bool = false) async -> DoctorReport? {
        var comps = URLComponents(url: AetherConfig.sidecarBaseURL.appendingPathComponent("doctor"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "online", value: online ? "true" : "false")]
        guard let url = comps?.url else { return nil }
        var request = URLRequest(url: url)
        request.timeoutInterval = 25
        applySidecarAuth(&request)
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return nil }
            return DoctorReport.parse(data)
        } catch {
            return nil
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

    /// Talk mode: ask about what is at `point` (global top-left points).
    func talk(question: String, at point: CGPoint?, sessionId: String?,
              talkId: String? = nil) async throws -> TalkReply {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("talk")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["question": question]
        if let point {
            body["x"] = point.x
            body["y"] = point.y
        }
        if let sessionId { body["session_id"] = sessionId }
        if let talkId {
            // The answer also streams as talk_token events carrying this id.
            body["talk_id"] = talkId
            body["stream"] = true
        } else {
            body["stream"] = false
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode),
              let reply = TalkReply.parse(data) else {
            let msg = String(data: data, encoding: .utf8) ?? "Talk failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: msg])
        }
        return reply
    }

    // MARK: onboarding interview and tour

    func fetchOnboardingQuestions() async -> (questions: [OnboardingQuestion],
                                              answers: [String: String], memoryEnabled: Bool)? {
        guard let result = try? await URLSession.shared.data(
                for: sessionsRequest("onboarding/questions")),
              (result.1 as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: result.0) as? [String: Any] else {
            return nil
        }
        let qs = (obj["questions"] as? [[String: Any]] ?? []).compactMap { q -> OnboardingQuestion? in
            guard let id = q["id"] as? String, let text = q["question"] as? String else { return nil }
            return OnboardingQuestion(id: id, question: text,
                                      placeholder: q["placeholder"] as? String ?? "")
        }
        return (qs, obj["answers"] as? [String: String] ?? [:],
                obj["memory_enabled"] as? Bool ?? false)
    }

    /// Store the answers; returns a short status line for the view.
    func saveProfile(_ answers: [String: String]) async -> String {
        var request = sessionsRequest("onboarding/profile", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["answers": answers])
        guard let result = try? await URLSession.shared.data(for: request),
              (result.1 as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: result.0) as? [String: Any] else {
            return "Couldn't save — is the sidecar running?"
        }
        let refused = obj["refused"] as? [String] ?? []
        return refused.isEmpty ? "Saved." : "Saved, except \(refused.joined(separator: ", "))."
    }

    func fetchTour() async -> [TourExample] {
        guard let result = try? await URLSession.shared.data(for: sessionsRequest("onboarding/tour")),
              let obj = try? JSONSerialization.jsonObject(with: result.0) as? [String: Any] else {
            return []
        }
        return (obj["examples"] as? [[String: Any]] ?? []).compactMap(TourExample.parse)
    }

    // MARK: dictation and select-and-transform

    private func postJSON(_ path: String, _ body: [String: Any],
                          timeout: TimeInterval = 30) async throws -> [String: Any] {
        var request = sessionsRequest(path, method: "POST")
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode) else {
            let detail = obj["detail"] as? String ?? String(data: data, encoding: .utf8) ?? "failed"
            throw NSError(domain: "Aether", code: 1, userInfo: [NSLocalizedDescriptionKey: detail])
        }
        return obj
    }

    /// Spoken text → text to type in the app in front (tone per app, vocabulary).
    func cleanDictation(_ text: String, bundleId: String, app: String) async -> String? {
        let obj = try? await postJSON("dictation/clean",
                                      ["text": text, "bundle_id": bundleId, "app": app])
        return obj?["text"] as? String
    }

    /// Rewrite selected text following an instruction.
    func transformText(_ text: String, instruction: String, bundleId: String) async throws -> String {
        let obj = try await postJSON("dictation/transform",
                                     ["text": text, "instruction": instruction,
                                      "bundle_id": bundleId], timeout: 60)
        return obj["text"] as? String ?? ""
    }

    // MARK: conversations (chat window)

    private func sessionsRequest(_ path: String, method: String = "GET") -> URLRequest {
        var request = URLRequest(url: AetherConfig.sidecarBaseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        applySidecarAuth(&request)
        return request
    }

    func listSessions(limit: Int = 50) async throws -> [ChatSessionSummary] {
        var comps = URLComponents(url: AetherConfig.sidecarBaseURL.appendingPathComponent("sessions"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        var request = URLRequest(url: comps?.url ?? AetherConfig.sidecarBaseURL)
        request.timeoutInterval = 15
        applySidecarAuth(&request)
        let (data, _) = try await URLSession.shared.data(for: request)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let rows = obj?["sessions"] as? [[String: Any]] ?? []
        return rows.compactMap(ChatSessionSummary.parse)
    }

    /// One conversation's turns (goal, result, actions, status).
    func fetchSession(_ id: String) async throws -> [[String: Any]] {
        let (data, response) = try await URLSession.shared.data(for: sessionsRequest("sessions/\(id)"))
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NSError(domain: "Aether", code: 404,
                          userInfo: [NSLocalizedDescriptionKey: "Conversation not found"])
        }
        return obj["turns"] as? [[String: Any]] ?? []
    }

    func deleteSession(_ id: String) async {
        _ = try? await URLSession.shared.data(for: sessionsRequest("sessions/\(id)", method: "DELETE"))
    }

    /// Guide mode: plan steps for `goal` and start pointing at them. Returns the guide id.
    func startGuide(goal: String) async throws -> String {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("guide")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["goal": goal])
        applySidecarAuth(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = json["guide_id"] as? String else {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw NSError(domain: "Aether", code: 1, userInfo: [
                NSLocalizedDescriptionKey: msg ?? "Could not start the guide.",
            ])
        }
        return id
    }

    /// next | back | repeat | skip | stop | do_it
    func controlGuide(id: String, action: String) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("guide").appendingPathComponent(id)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["action": action])
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    /// Answer an ask_user question; nil or empty skips it.
    func submitAnswer(requestId: String, answer: String?) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("answer")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["request_id": requestId]
        if let answer, !answer.isEmpty { body["answer"] = answer }
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        applySidecarAuth(&request)
        _ = try? await URLSession.shared.data(for: request)
    }

    func reportVoiceMetrics(sttMs: Double? = nil, ttsMs: Double? = nil, voiceRttMs: Double? = nil,
                            firstAudioMs: Double? = nil) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("metrics/voice")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = [:]
        if let sttMs { body["stt_ms"] = sttMs }
        if let ttsMs { body["tts_ms"] = ttsMs }
        if let voiceRttMs { body["voice_rtt_ms"] = voiceRttMs }
        if let firstAudioMs { body["first_audio_ms"] = firstAudioMs }
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

    /// Persistent subscribe-only stream for proactive/global events (Phase 11).
    /// Reconnects after drops (e.g. a sidecar restart). Delivers run_request,
    /// say, and fleet events that fire outside any /run.
    func subscribeEvents(onEvent: @escaping (SidecarEvent) -> Void) async {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("events")
        while !Task.isCancelled {
            do {
                var request = URLRequest(url: url)
                request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                request.timeoutInterval = 3600
                applySidecarAuth(&request)
                let (bytes, response) = try await URLSession.shared.bytes(for: request)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    continue
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
                        switch type {
                        case "guide_step", "guide_done", "talk_token", "talk_done",
                             "confirm_request", "question":
                            for event in SidecarEvent.parse(obj, fallbackGoal: "") { onEvent(event) }
                        case "run_request":
                            if let g = obj["goal"] as? String { onEvent(.runRequest(goal: g)) }
                        case "say":
                            if let t = obj["text"] as? String { onEvent(.say(t)) }
                        case "fleet":
                            onEvent(.fleet(obj))
                        default:
                            break
                        }
                    } else {
                        lineBuffer.append(ch)
                    }
                }
            } catch {
                if Task.isCancelled { break }
                try? await Task.sleep(nanoseconds: 2_000_000_000)  // reconnect
            }
        }
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

        var body: [String: Any] = [
            "goal": goal,
            "careful": options.careful,
            "local_only": options.localOnly,
            "stream": options.stream,
            "narrate": false,
        ]
        if let sessionId = options.sessionId { body["session_id"] = sessionId }
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
                      obj["type"] is String else { continue }

                let events = SidecarEvent.parse(obj, fallbackGoal: goal)
                await MainActor.run {
                    for event in events { onEvent(event) }
                }
            } else {
                lineBuffer.append(ch)
            }
        }
    }

    func transcribe(wavData: Data, useVocabulary: Bool = false) async throws -> String {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("stt")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "audio_base64": wavData.base64EncodedString(),
            "use_vocabulary": useVocabulary,
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
