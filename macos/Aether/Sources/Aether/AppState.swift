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
        stopController.start()
        pttHotkey.start()
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
        if beta.nativeEffectors {
            if !nativeEffector.isRunning {
                nativeEffector.start()
            }
        } else {
            nativeEffector.stop()
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
        world.reset()
        world.goal = trimmed
        world.transcript = trimmed
        world.status = "working"
        refreshHUD()

        client.run(goal: trimmed) { [weak self] event in
            guard let self else { return }
            switch event {
            case .runStart(_, let g):
                self.world.goal = g
                self.world.transcript = g
                self.world.status = "working"
            case .hud(let hud):
                self.world.apply(hud: hud)
            case .say(let text):
                Task { await self.speakWithBargeIn(text) }
            case .done(let result, let snap):
                self.lastResult = result
                self.world.apply(world: snap)
                self.world.status = "idle"
                self.world.currentStep = result
                Task { await self.speakWithBargeIn(result) }
            case .error(let msg):
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

    private func speakConfirmationPrompt(_ description: String) async {
        await tts.speak("Confirm: \(description). Say yes or no.")
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
            if voiceSettings.prefersGroqSTT, client.healthOK {
                text = try await client.transcribe(wavData: wav)
            } else if stt.speechAuthorized {
                text = try await stt.transcribe(wavData: wav)
            } else if client.healthOK {
                text = try await client.transcribe(wavData: wav)
            }
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
        if text.lowercased().contains("stop") {
            handleStop()
            return
        }
        submitGoal(text)
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
