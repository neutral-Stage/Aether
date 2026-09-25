import AppKit
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var goalText = ""
    @Published var showOnboarding = false
    @Published var showMainWindow = false
    @Published var lastResult = ""
    @Published var isPTTHeld = false
    @Published var bargeInEnabled = true
    @Published var ambientActive = false
    @Published var feedbackText = ""
    @Published var fleetSessionStates: [String: String] = [:]

    var fleetActiveCount: Int {
        fleetSessionStates.values.filter {
            ["starting", "running", "awaiting_input"].contains($0)
        }.count
    }

    private var voiceSettings = VoiceSettings()
    private var betaSettings = BetaSettings()
    private var pendingConfirmId: String?
    private var pendingQuestionId: String?
    /// Conversation of the last run; a request within `followUpWindow` of it
    /// continues the conversation, so "now do the same for…" has context.
    private var sessionId: String?
    private var lastRunEnded: Date?
    private let followUpWindow: TimeInterval = 600

    let world = WorldModel()
    let client = OrchestratorClient()
    let audio = AudioEngine()
    let stt = STTBridge()
    let tts = TTSBridge()
    lazy var voice = VoicePipeline(audio: audio, stt: stt, tts: tts)
    let wakeWord = WakeWordDetector()
    lazy var ambient = AmbientListeningController(audio: audio, wake: wakeWord)
    let hud = HUDPanel()
    let commandBar = CommandBarPanel()
    let confirmation = ConfirmationPanel()
    let questionPanel = QuestionPanel()
    let overlay = OverlayController()
    private let talkHotkey = ModifierHoldController()
    @Published var isTalkHeld = false
    private var talkPointer: CGPoint?
    private var talkSessionId: String?
    // Streamed talk answers: spoken clause by clause as talk_token events arrive.
    private var streamingTalkId: String?
    private var talkSplitter = ClauseSplitter()
    private var talkPartial = ""
    private var talkStreamed = false
    private var talkDoneSeen = false
    private var speechChain: Task<Void, Never>?
    // An agent run's streamed reply (token events), per step.
    private var streamStep = -1
    private var streamText = ""
    private var activeGuideId: String?
    let nativeEffector = NativeEffectorServer(port: AetherConfig.nativeEffectorPort)
    lazy var stopController = StopController { [weak self] in
        self?.handleStop()
    }
    private let pttHotkey = PTTHotkeyController()
    private let commandBarHotkey = CommandBarHotkeyController()
    let updateChecker = SparkleUpdateController()
    let sidecar = SidecarSupervisor()
    lazy var realtimeSession = RealtimeVoiceSession()
    lazy var screenStream = ScreenStreamManager()

    init() {
        showOnboarding = !AetherConfig.hasCompletedOnboarding
        voice.configure(energyThreshold: 0.02)
        voice.onMetrics = { [weak self] sttMs, ttsMs, voiceRttMs in
            Task { await self?.client.reportVoiceMetrics(sttMs: sttMs, ttsMs: ttsMs, voiceRttMs: voiceRttMs) }
        }
        pttHotkey.onBegin = { [weak self] in
            Task { @MainActor in self?.beginPTT() }
        }
        pttHotkey.onEnd = { [weak self] in
            Task { @MainActor in await self?.endPTT() }
        }
        talkHotkey.onBegin = { [weak self] point in
            Task { @MainActor in self?.beginTalk(at: point) }
        }
        talkHotkey.onEnd = { [weak self] in
            Task { @MainActor in await self?.endTalk() }
        }
        talkHotkey.onCancel = { [weak self] in
            Task { @MainActor in self?.cancelTalk() }
        }
        commandBarHotkey.onToggle = { [weak self] in
            Task { @MainActor in self?.toggleCommandBar() }
        }
        ambient.onWake = { [weak self] in
            Task { @MainActor in await self?.handleWakeWord() }
        }
        wakeWord.onWake = { [weak self] in
            Task { @MainActor in await self?.handleWakeWord() }
        }
    }

    func bootstrap() {
        _ = AuditKeychain.ensureKey()
        sidecar.start()  // launch + supervise our own sidecar (Phase 7)
        // Persistent event stream: proactive triggers can auto-run when idle
        // (Phase 11). Server-side double-gates auto_run, so a run_request here
        // is already approved to execute.
        Task { [weak self] in
            await self?.client.subscribeEvents { event in
                Task { @MainActor in self?.handleBackgroundEvent(event) }
            }
        }
        nativeEffector.start()  // loopback capture endpoint for the sidecar
        stopController.start()
        pttHotkey.start()
        talkHotkey.start()
        commandBarHotkey.start()
        audio.refreshMicPermission()
        stt.refreshAuthorization()
        Task {
            await client.checkHealth()
            if let settings = await client.fetchVoiceConfig() {
                voiceSettings = settings
                bargeInEnabled = settings.bargeIn
                voice.configure(energyThreshold: settings.vadEnergyThreshold)
                tts.voiceSettings = settings
                tts.groqSynthesize = { [weak self] text in
                    guard let self else { return Data() }
                    return try await self.client.synthesize(text: text)
                }
                tts.groqSynthesizeStream = { [weak self] text in
                    guard let self else { return Data() }
                    return try await self.client.synthesizeStream(text: text)
                }
                if settings.usesRealtimeMode {
                    await realtimeSession.connect()
                }
            }
            if let beta = await client.fetchBetaConfig() {
                betaSettings = beta
                applyBetaSettings(beta)
            }
            refreshHUD()
            await updateChecker.checkIfNeeded()
        }
    }

    private func applyBetaSettings(_ beta: BetaSettings) {
        // The loopback server always runs (screen capture); click/type through
        // it stays behind the beta flag.
        nativeEffector.allowInvoke = beta.nativeEffectors
        if !nativeEffector.isRunning {
            nativeEffector.start()
        }
        if beta.ambientListening || beta.wakeWord {
            ambientActive = true
            ambient.start(threshold: voiceSettings.vadEnergyThreshold)
            wakeWord.engine = beta.wakeWordEngine == "porcupine" ? .porcupine : .energy
            wakeWord.isListening = beta.wakeWord
        }
        if beta.continuousScreenStream {
            startScreenStream(fps: beta.screenStreamFPS)
        }
        CrashReporter.installIfEnabled(beta.crashReporting)
    }

    private func startScreenStream(fps: Double) {
        Task {
            await screenStream.start(fps: fps) { [weak self] width, height, hash in
                guard let self else { return }
                Task {
                    await self.client.postScreenPercept(
                        width: width,
                        height: height,
                        fps: fps,
                        note: "frame \(hash)"
                    )
                }
            }
        }
    }

    func toggleCommandBar() {
        let binding = Binding<String>(
            get: { [weak self] in self?.goalText ?? "" },
            set: { [weak self] in self?.goalText = $0 }
        )
        commandBar.toggle(
            goalText: binding,
            isRunning: client.isRunning,
            onSubmit: { [weak self] goal in self?.submitGoal(goal) }
        )
    }

    func refreshHUD() {
        hud.show(
            world: world,
            audio: audio,
            voice: voice,
            isRunning: client.isRunning,
            sidecarOK: client.healthOK,
            ambientActive: ambientActive,
            fleetActiveCount: fleetActiveCount,
            onStop: { [weak self] in self?.handleStop() }
        )
        hud.setClickThrough(!client.isRunning && !audio.isRecording && pendingConfirmId == nil)
    }

    func submitGoal(_ goal: String) {
        let trimmed = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if GuideIntent.isGuideRequest(trimmed), activeGuideId == nil {
            startGuide(trimmed)
            return
        }
        world.reset()
        world.goal = trimmed
        world.transcript = trimmed
        world.status = "working"
        refreshHUD()

        var options = RunOptions()
        if let sid = sessionId, let ended = lastRunEnded,
           Date().timeIntervalSince(ended) < followUpWindow {
            options.sessionId = sid
        }
        client.run(goal: trimmed, options: options) { [weak self] event in
            guard let self else { return }
            switch event {
            case .runStart(_, let g):
                self.streamStep = -1
                self.streamText = ""
                self.world.goal = g
                self.world.transcript = g
                self.world.status = "working"
            case .hud(let hud):
                self.world.apply(hud: hud)
            case .say(let text):
                Task { await self.speakWithBargeIn(text) }
            case .done(let result, let snap):
                self.lastRunEnded = Date()
                self.questionPanel.hide()
                self.pendingQuestionId = nil
                self.lastResult = result
                self.world.apply(world: snap)
                self.world.status = "idle"
                self.world.currentStep = result
                Task { await self.speakWithBargeIn(result) }
            case .error(let msg):
                self.lastRunEnded = Date()
                self.questionPanel.hide()
                self.pendingQuestionId = nil
                self.lastResult = msg
                self.world.status = "idle"
                self.world.currentStep = msg
            case .stopped:
                self.world.status = "stopped"
                self.world.currentStep = "Stopped"
            case .ping:
                break
            case .confirmRequest(let requestId, let description):
                self.showConfirmation(requestId: requestId, description: description)
            case .fleet(let payload):
                if let sid = payload["session_id"] as? String,
                   let state = payload["state"] as? String {
                    self.fleetSessionStates[sid] = state
                }
            case .runRequest:
                break  // proactive auto-run is handled by the persistent /events stream
            case .question(let requestId, let question, let options):
                self.showQuestion(requestId: requestId, question: question, options: options)
            case .session(let sid):
                self.sessionId = sid
            case .pointer(let targets):
                self.overlay.show(targets: targets)
            case .guideStep, .guideDone, .talkToken, .talkDone:
                self.handleBackgroundEvent(event)
            case let .token(step, text):
                // The model's reply as it streams, shown until the next action.
                if step != self.streamStep {
                    self.streamStep = step
                    self.streamText = ""
                }
                self.streamText += text
                self.world.currentStep = self.streamText
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            case .step(let payload):
                if (payload["type"] as? String) == "tool_call",
                   let desc = payload["description"] as? String {
                    self.world.currentStep = desc
                }
            }
            self.refreshHUD()
        }
    }

    private func showConfirmation(requestId: String, description: String) {
        pendingConfirmId = requestId
        world.currentStep = "Confirm: \(description)"
        refreshHUD()
        confirmation.show(
            description: description,
            onApprove: { [weak self] in
                guard let self else { return }
                Task { await self.client.submitConfirmation(requestId: requestId, approved: true) }
                self.pendingConfirmId = nil
                self.refreshHUD()
            },
            onDecline: { [weak self] in
                guard let self else { return }
                Task { await self.client.submitConfirmation(requestId: requestId, approved: false) }
                self.pendingConfirmId = nil
                self.refreshHUD()
            }
        )
        Task { await speakConfirmationPrompt(description) }
    }

    /// Events from the persistent /events stream (proactive runs, guide mode).
    func handleBackgroundEvent(_ event: SidecarEvent) {
        switch event {
        case let .talkToken(id, text):
            guard id == streamingTalkId else { return }
            talkPartial += text
            world.currentStep = talkPartial.trimmingCharacters(in: .whitespacesAndNewlines)
            refreshHUD()
            for clause in talkSplitter.feed(text) { speakInOrder(clause) }
        case let .talkDone(id, _):
            guard id == streamingTalkId else { return }
            if let rest = talkSplitter.flush() { speakInOrder(rest) }
            talkDoneSeen = true
        case .runRequest(let goal):
            if !client.isRunning { submitGoal(goal) }
        case let .guideStep(id, index, total, say, target):
            activeGuideId = id
            world.status = "guiding"
            world.currentStep = "Step \(index + 1) of \(total): \(say)"
            if let target {
                overlay.show(targets: [target], hold: 300)
            } else {
                overlay.clear()
            }
            refreshHUD()
            Task { await speakWithBargeIn(say) }
        case let .guideDone(id, status):
            guard id == activeGuideId else { return }
            activeGuideId = nil
            overlay.clear()
            world.status = "idle"
            world.currentStep = status == "done" ? "All done." : "Guide stopped."
            refreshHUD()
            if status == "done" { Task { await speakWithBargeIn("All done.") } }
        default:
            break
        }
    }

    func startGuide(_ request: String) {
        world.reset()
        world.goal = request
        world.status = "working"
        world.currentStep = "Working out the steps…"
        refreshHUD()
        Task {
            do {
                activeGuideId = try await client.startGuide(goal: request)
            } catch {
                world.status = "idle"
                world.currentStep = error.localizedDescription
                lastResult = error.localizedDescription
                refreshHUD()
            }
        }
    }

    private func showQuestion(requestId: String, question: String, options: [String]) {
        pendingQuestionId = requestId
        world.currentStep = "Question: \(question)"
        refreshHUD()
        questionPanel.show(question: question, options: options) { [weak self] answer in
            self?.answerQuestion(answer)
        }
    }

    /// Send the answer (typed, picked, or spoken with push-to-talk); nil skips.
    func answerQuestion(_ answer: String?) {
        guard let requestId = pendingQuestionId else { return }
        pendingQuestionId = nil
        questionPanel.hide()
        Task { await client.submitAnswer(requestId: requestId, answer: answer) }
        refreshHUD()
    }

    /// Forget the current conversation; the next request starts a new one.
    func newConversation() {
        sessionId = nil
        lastRunEnded = nil
    }

    private func speakConfirmationPrompt(_ description: String) async {
        await tts.speak("Confirm: \(description). Say yes or no.")
    }

    /// Queue speech behind whatever is already being said (streamed talk clauses).
    private func speakInOrder(_ text: String) {
        talkStreamed = true
        let previous = speechChain
        speechChain = Task { @MainActor [weak self] in
            await previous?.value
            guard let self, !Task.isCancelled else { return }
            await self.speakWithBargeIn(text)
        }
    }

    func speakWithBargeIn(_ text: String) async {
        guard bargeInEnabled else {
            let start = Date()
            await tts.speak(text)
            await client.reportVoiceMetrics(ttsMs: Date().timeIntervalSince(start) * 1000)
            return
        }
        await voice.speakWithBargeIn(text) { [weak self] in
            guard let self else { return }
            self.world.currentStep = "Listening…"
            self.refreshHUD()
        }
        if !voice.partialTranscript.isEmpty {
            world.transcript = voice.partialTranscript
            handleVoiceConfirmation(voice.partialTranscript)
        }
    }

    private func handleVoiceConfirmation(_ text: String) {
        guard pendingConfirmId != nil else { return }
        let lower = text.lowercased()
        if lower.contains("yes") || lower.contains("confirm") || lower.contains("proceed") {
            Task {
                await client.submitConfirmation(requestId: pendingConfirmId!, approved: true)
                pendingConfirmId = nil
                confirmation.hide()
                refreshHUD()
            }
        } else if lower.contains("no") || lower.contains("cancel") || lower.contains("stop") {
            Task {
                await client.submitConfirmation(requestId: pendingConfirmId!, approved: false)
                pendingConfirmId = nil
                confirmation.hide()
                refreshHUD()
            }
        }
    }

    func handleStop() {
        let started = Date()
        Task {
            _ = await client.stop()
        }
        client.cancelRun()
        voice.stopAll()
        confirmation.hide()
        pendingConfirmId = nil
        questionPanel.hide()
        pendingQuestionId = nil
        lastRunEnded = Date()
        overlay.clear()
        speechChain?.cancel()
        speechChain = nil
        streamingTalkId = nil
        if isTalkHeld { cancelTalk() }
        if let guideId = activeGuideId {
            activeGuideId = nil
            Task { await client.controlGuide(id: guideId, action: "stop") }
        }
        world.status = "stopped"
        world.currentStep = "Stopped"
        refreshHUD()
        let localMs = Date().timeIntervalSince(started) * 1000
        if localMs > 200 {
            NSLog("[Aether] STOP local latency %.1f ms (budget 200 ms)", localMs)
        }
    }

    func beginPTT() {
        guard !isPTTHeld else { return }
        isPTTHeld = true
        voice.stopAll()
        try? audio.startRecording()
        refreshHUD()
    }

    func endPTT() async {
        guard isPTTHeld else { return }
        isPTTHeld = false
        let wav = audio.stopRecording()
        refreshHUD()
        guard !wav.isEmpty else { return }

        let roundStart = Date()
        let sttStart = Date()
        var text = ""
        do {
            text = try await transcribe(wav)
        } catch {
            lastResult = error.localizedDescription
            refreshHUD()
            return
        }

        let sttMs = Date().timeIntervalSince(sttStart) * 1000
        let voiceRttMs = Date().timeIntervalSince(roundStart) * 1000
        await client.reportVoiceMetrics(sttMs: sttMs, voiceRttMs: voiceRttMs)

        world.transcript = text
        wakeWord.processPartialTranscript(text)
        if pendingConfirmId != nil {
            handleVoiceConfirmation(text)
            return
        }
        if pendingQuestionId != nil {
            answerQuestion(text)
            return
        }
        if let guideId = activeGuideId {
            if let action = GuideIntent.control(for: text) {
                Task { await client.controlGuide(id: guideId, action: action) }
            } else {
                world.currentStep = "Say next, back, repeat, skip, stop, or do it for me."
                refreshHUD()
            }
            return
        }
        if text.lowercased().contains("stop") {
            handleStop()
            return
        }
        submitGoal(text)
    }

    private func transcribe(_ wav: Data) async throws -> String {
        if voiceSettings.prefersGroqSTT, client.healthOK {
            return try await client.transcribe(wavData: wav)
        } else if stt.speechAuthorized {
            return try await stt.transcribe(wavData: wav)
        } else if client.healthOK {
            return try await client.transcribe(wavData: wav)
        }
        return ""
    }

    // MARK: - Talk mode (hold ⌃⌥, ask about what the mouse points at)

    func beginTalk(at point: CGPoint) {
        guard !isPTTHeld, !isTalkHeld else { return }
        isTalkHeld = true
        talkPointer = point
        voice.stopAll()
        overlay.clear()
        try? audio.startRecording()
        world.currentStep = "Listening… release to ask"
        refreshHUD()
    }

    func cancelTalk() {
        guard isTalkHeld else { return }
        isTalkHeld = false
        _ = audio.stopRecording()
        world.currentStep = ""
        refreshHUD()
    }

    func endTalk() async {
        guard isTalkHeld else { return }
        isTalkHeld = false
        let wav = audio.stopRecording()
        guard !wav.isEmpty else { refreshHUD(); return }
        world.currentStep = "Looking…"
        refreshHUD()
        do {
            let question = try await transcribe(wav).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !question.isEmpty else {
                world.currentStep = ""
                refreshHUD()
                return
            }
            world.transcript = question
            if question.lowercased() == "stop" {
                handleStop()
                return
            }
            let talkId = String(UUID().uuidString.lowercased().prefix(12))
            streamingTalkId = talkId
            talkSplitter = ClauseSplitter()
            talkPartial = ""
            talkStreamed = false
            talkDoneSeen = false
            let reply = try await client.talk(question: question, at: talkPointer,
                                              sessionId: talkSessionId, talkId: talkId)
            talkSessionId = reply.sessionId
            lastResult = reply.answer
            world.currentStep = reply.answer
            overlay.show(targets: reply.targets)
            refreshHUD()
            // The streamed events and the reply travel separately: give the last
            // events a moment, then speak whatever did not arrive as a stream.
            for _ in 0 ..< 8 where !talkDoneSeen {
                try? await Task.sleep(nanoseconds: 50_000_000)
            }
            streamingTalkId = nil
            if !talkStreamed {
                await speakWithBargeIn(reply.answer)
            } else if !talkDoneSeen, let rest = talkSplitter.flush() {
                speakInOrder(rest)
            }
        } catch {
            lastResult = error.localizedDescription
            world.currentStep = error.localizedDescription
            refreshHUD()
        }
    }

    private func handleWakeWord() async {
        guard !client.isRunning, !isPTTHeld else { return }
        world.currentStep = "Wake word — listening…"
        refreshHUD()
        beginPTT()
    }

    func submitFeedback() {
        let msg = feedbackText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !msg.isEmpty else { return }
        Task {
            await client.submitFeedback(message: msg)
            feedbackText = ""
            lastResult = "Feedback sent — thank you!"
            refreshHUD()
        }
    }
}

struct SidecarStatusLabel: View {
    @ObservedObject var supervisor: SidecarSupervisor

    var body: some View {
        Text(label).font(.caption).foregroundStyle(.secondary)
    }

    private var label: String {
        switch supervisor.status {
        case .healthy, .external: return "Sidecar OK"
        case .starting, .idle: return "Starting sidecar…"
        case .failed(let why): return "Sidecar: \(why)"
        }
    }
}

struct MainWindowView: View {
    @ObservedObject var app: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Aether")
                .font(.title.bold())
            TextField("What should I do?", text: $app.goalText)
                .textFieldStyle(.roundedBorder)
                .onSubmit { app.submitGoal(app.goalText) }

            Toggle("Barge-in (interrupt speech)", isOn: $app.bargeInEnabled)

            if let update = app.updateChecker.updateAvailable {
                HStack {
                    Text("Update \(update.latestVersion) available")
                        .font(.caption)
                        .foregroundStyle(.orange)
                    Link("Download", destination: update.releaseURL)
                        .font(.caption)
                }
            }

            Text("Global PTT: hold ⌃Space · Command bar: ⌥Space")
                .font(.caption2)
                .foregroundStyle(.secondary)

            if app.ambientActive {
                Label("Ambient listening active", systemImage: "ear")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            HStack {
                Button("Run") { app.submitGoal(app.goalText) }
                    .disabled(app.client.isRunning || app.goalText.isEmpty)
                Button(app.isPTTHeld ? "Release to send" : "Hold to talk") {
                    if app.isPTTHeld {
                        Task { await app.endPTT() }
                    } else {
                        app.beginPTT()
                    }
                }
                .simultaneousGesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { _ in if !app.isPTTHeld { app.beginPTT() } }
                        .onEnded { _ in Task { await app.endPTT() } }
                )
                Spacer()
                Circle()
                    .fill(app.client.healthOK ? Color.green : Color.orange)
                    .frame(width: 8, height: 8)
                SidecarStatusLabel(supervisor: app.sidecar)
            }

            Divider()
            CatalogView(client: app.client)

            Divider()
            KeysView(onSaved: { app.sidecar.restart() })

            Divider()
            FleetView(client: app.client)

            Divider()
            GraphView(client: app.client)

            Divider()
            MCPSettingsView(client: app.client)

            Divider()
            SkillsView(client: app.client)

            Divider()
            Text("Beta feedback")
                .font(.caption.weight(.semibold))
            TextField("Send feedback…", text: $app.feedbackText)
                .textFieldStyle(.roundedBorder)
            Button("Submit feedback") { app.submitFeedback() }
                .disabled(app.feedbackText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            if !app.world.transcript.isEmpty {
                Text("Transcript: \(app.world.transcript)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            if !app.lastResult.isEmpty {
                Text(app.lastResult)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(4)
            }

            Text(AccessibilityReader.screenContextSummary(maxElements: 8))
                .font(.caption2.monospaced())
                .foregroundStyle(.tertiary)
                .lineLimit(6)
        }
        .padding()
        .frame(minWidth: 420, minHeight: 360)
    }
}
