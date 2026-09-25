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
    /// On-device "Hey Aether …" (beta.wake_word_engine: speech).
    lazy var speechWake: SpeechWakeListener = {
        let listener = SpeechWakeListener(stt: stt)
        listener.isBusy = { [weak self] in
            guard let self else { return true }
            return self.isPTTHeld || self.isTalkHeld || self.tts.isSpeaking || self.client.isRunning
        }
        listener.onCommand = { [weak self] command in
            guard let self else { return }
            self.world.transcript = command
            self.world.currentStep = "Heard: \(command)"
            self.refreshHUD()
            self.routeSpokenText(command)
        }
        return listener
    }()
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
    private var fillerTask: Task<Void, Never>?
    /// Said when an answer takes a moment to start (synthesized ahead of time).
    static let fillers = ["One moment.", "Let me look.", "Okay, checking."]
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
    /// ⌃⌘A opens the chat window.
    private let chatHotkey = CommandBarHotkeyController(modifiers: [.control, .command], keyCode: 0)
    lazy var chat: ChatStore = {
        let store = ChatStore(client: client)
        store.app = self
        return store
    }()
    private let chatWindow = ChatWindowController()
    /// ⌃⌥D dictates into the focused field; ⌃⌥T rewrites the selection.
    private let dictationHotkey = CommandBarHotkeyController(modifiers: [.control, .option],
                                                             keyCode: 2)
    private let transformHotkey = CommandBarHotkeyController(modifiers: [.control, .option],
                                                             keyCode: 17)
    lazy var dictation = DictationController(audio: audio, client: client)
    /// Quick skills on ⌃⌥1–9 (prompt + capture + destination).
    lazy var quickSkills = QuickSkillsController(client: client, audio: audio)
    private let transformPanel = TransformPanel()
    private let chipsPanel = ChipsPanel()
    private let chipsHotkey = CommandBarHotkeyController(modifiers: [.control, .option], keyCode: 8)
    let updateChecker = SparkleUpdateController()
    let sidecar = SidecarSupervisor()
    lazy var realtimeSession = RealtimeVoiceSession()
    private lazy var realtimePlayer = PCMStreamPlayer()
    private lazy var realtimeMic = RealtimeMicStreamer()
    /// Realtime voice replaces the transcribe → run → speak pipeline for push-to-talk.
    private var realtimeActive: Bool {
        voiceSettings.usesRealtimeMode && realtimeSession.isConnected
    }
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
        chatHotkey.onToggle = { [weak self] in
            Task { @MainActor in self?.openChat() }
        }
        dictationHotkey.onToggle = { [weak self] in
            Task { @MainActor in
                guard let self, !self.isPTTHeld else { return }
                await self.dictation.toggle()
            }
        }
        chipsHotkey.onToggle = { [weak self] in
            Task { @MainActor in await self?.showChips() }
        }
        transformHotkey.onToggle = { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                await self.transformPanel.begin(client: self.client) { self.showStatus($0) }
            }
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
        chatHotkey.start()
        dictationHotkey.start()
        transformHotkey.start()
        chipsHotkey.start()
        dictation.onStatus = { [weak self] status in self?.showStatus(status) }
        quickSkills.onStatus = { [weak self] status in self?.showStatus(status) }
        quickSkills.speak = { [weak self] text in await self?.speakWithBargeIn(text) }
        Task { await quickSkills.reload() }
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
                await tts.prewarm(Self.fillers)
                if settings.usesRealtimeMode {
                    wireRealtime()
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
        if beta.wakeWord && beta.wakeWordEngine == "speech" {
            // On-device recognizer only: no energy gate, audio stays on the Mac.
            ambientActive = true
            speechWake.start()
        } else if beta.ambientListening || beta.wakeWord {
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

    /// A short status line in the HUD (dictation, transform).
    private func showStatus(_ text: String) {
        world.currentStep = text
        refreshHUD()
    }

    /// Open the chat window, optionally on one conversation.
    func openChat(session: String? = nil) {
        chatWindow.show(store: chat)
        if let session { Task { await chat.open(session) } }
    }

    /// Run a request. The chat window passes its conversation and an observer
    /// that receives every run event; other callers continue the last
    /// conversation when it ended recently.
    func submitGoal(_ goal: String, chatSession: String? = nil,
                    observer: ((SidecarEvent) -> Void)? = nil) {
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
        if observer != nil {
            options.sessionId = chatSession
        } else if let sid = sessionId, let ended = lastRunEnded,
                  Date().timeIntervalSince(ended) < followUpWindow {
            options.sessionId = sid
        }
        client.run(goal: trimmed, options: options) { [weak self] event in
            guard let self else { return }
            observer?(event)
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
                if self.pendingConfirmId != requestId {
                    self.showConfirmation(requestId: requestId, description: description)
                }
            case let .draftRequest(requestId, description, fields):
                if self.pendingConfirmId != requestId {
                    self.showDraft(requestId: requestId, description: description, fields: fields)
                }
            case .fleet(let payload):
                if let sid = payload["session_id"] as? String,
                   let state = payload["state"] as? String {
                    self.fleetSessionStates[sid] = state
                }
            case .runRequest:
                break  // proactive auto-run is handled by the persistent /events stream
            case .question(let requestId, let question, let options):
                if self.pendingQuestionId != requestId {
                    self.showQuestion(requestId: requestId, question: question, options: options)
                }
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

    private func showDraft(requestId: String, description: String, fields: [DraftField]) {
        pendingConfirmId = requestId
        world.currentStep = "Check the draft before it goes out"
        refreshHUD()
        confirmation.showDraft(
            description: description, fields: fields,
            onSend: { [weak self] edits in
                guard let self else { return }
                Task { await self.client.submitConfirmation(requestId: requestId, approved: true,
                                                            edits: edits) }
                self.pendingConfirmId = nil
                self.refreshHUD()
            },
            onDecline: { [weak self] in
                guard let self else { return }
                Task { await self.client.submitConfirmation(requestId: requestId, approved: false) }
                self.pendingConfirmId = nil
                self.refreshHUD()
            })
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
        case let .draftRequest(requestId, description, fields):
            guard pendingConfirmId != requestId else { return }
            showDraft(requestId: requestId, description: description, fields: fields)
        case let .confirmRequest(requestId, description):
            // Runs started elsewhere (realtime voice, triggers) ask here too; a run's own
            // stream may deliver the same request, so show it once.
            guard pendingConfirmId != requestId else { return }
            showConfirmation(requestId: requestId, description: description)
        case let .question(requestId, question, options):
            guard pendingQuestionId != requestId else { return }
            showQuestion(requestId: requestId, question: question, options: options)
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
    /// A filler is queued the same way but doesn't count as the answer.
    private func speakInOrder(_ text: String, isAnswer: Bool = true) {
        if isAnswer {
            talkStreamed = true
            fillerTask?.cancel()
        }
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
        fillerTask?.cancel()
        realtimeMic.stop()
        realtimePlayer.stop()
        dictation.cancel()
        transformPanel.hide()
        chipsPanel.hide()
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

    private func wireRealtime() {
        realtimeSession.onAudioDelta = { [weak self] audio, itemId in
            self?.realtimePlayer.append(pcm16: audio, itemId: itemId)
        }
        realtimeSession.onTextDelta = { [weak self] delta in
            guard let self else { return }
            self.world.currentStep = self.realtimeSession.lastTranscript.isEmpty
                ? delta : self.realtimeSession.lastTranscript
            self.refreshHUD()
        }
        realtimeSession.onReplyDone = { [weak self] text in
            guard let self, !text.isEmpty else { return }
            self.lastResult = text
        }
        realtimeMic.onChunk = { [weak self] chunk in
            guard let self, self.isPTTHeld else { return }
            Task { await self.realtimeSession.sendAudio(pcm16: chunk) }
        }
    }

    func beginPTT() {
        guard !isPTTHeld, dictation.state == .idle, quickSkills.recordingSkillId == nil
        else { return }
        if realtimeActive {
            isPTTHeld = true
            if realtimePlayer.isPlaying {
                // Talking over the reply: stop it where the user stopped hearing it.
                let heard = realtimePlayer.heardMs
                let item = realtimePlayer.itemId
                realtimePlayer.stop()
                Task { await realtimeSession.interrupt(itemId: item, audioEndMs: heard) }
            }
            Task { await realtimeSession.clearInput() }
            do {
                try realtimeMic.start()
                world.currentStep = "Listening…"
            } catch {
                world.currentStep = error.localizedDescription
            }
            refreshHUD()
            return
        }
        isPTTHeld = true
        voice.stopAll()
        try? audio.startRecording()
        refreshHUD()
    }

    func endPTT() async {
        guard isPTTHeld else { return }
        if realtimeActive {
            isPTTHeld = false
            realtimeMic.stop()
            world.currentStep = "Thinking…"
            refreshHUD()
            await realtimeSession.commitAudio()
            return
        }
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
        routeSpokenText(text)
    }

    /// What to do with something the user said: answer a pending confirmation or
    /// question, steer a guide, stop, or run it as a request.
    private func routeSpokenText(_ text: String) {
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
        guard !isPTTHeld, !isTalkHeld, dictation.state == .idle else { return }
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
        // First audio: from releasing the keys to the first sound of the answer.
        let released = Date()
        talkStreamed = false
        tts.onPlaybackStart = { [weak self] in
            let ms = Date().timeIntervalSince(released) * 1000
            Task { await self?.client.reportVoiceMetrics(firstAudioMs: ms) }
        }
        fillerTask?.cancel()
        fillerTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 900_000_000)
            guard let self, !Task.isCancelled, !self.talkStreamed, !self.tts.isSpeaking,
                  let filler = Self.fillers.randomElement() else { return }
            self.speakInOrder(filler, isAnswer: false)
        }
        defer { fillerTask?.cancel() }
        world.currentStep = "Looking…"
        refreshHUD()
        do {
            let question = try await transcribe(wav).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !question.isEmpty else {
                fillerTask?.cancel()
                world.currentStep = ""
                refreshHUD()
                return
            }
            world.transcript = question
            if question.lowercased() == "stop" {
                fillerTask?.cancel()
                handleStop()
                return
            }
            try await askTalk(question, at: talkPointer)
        } catch {
            lastResult = error.localizedDescription
            world.currentStep = error.localizedDescription
            refreshHUD()
        }
    }

    /// Ask about what is on screen (talk mode, or a chip): the answer streams, is
    /// spoken clause by clause, and points at things.
    func askTalk(_ question: String, at point: CGPoint?) async throws {
        let talkId = String(UUID().uuidString.lowercased().prefix(12))
        streamingTalkId = talkId
        talkSplitter = ClauseSplitter()
        talkPartial = ""
        talkStreamed = false
        talkDoneSeen = false
        let reply = try await client.talk(question: question, at: point,
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
            speakInOrder(reply.answer)
        } else if !talkDoneSeen, let rest = talkSplitter.flush() {
            speakInOrder(rest)
        }
    }

    // MARK: - Suggestion chips (⌃⌥C)

    func showChips() async {
        let point = NSEvent.mouseLocation                       // for placing the panel
        let pointer = ModifierHoldController.mouseTopLeft()     // for the sidecar
        let selection = TextInsertion.secureInputActive() ? "" : await TextInsertion.selectedText()
        showStatus("Thinking of suggestions…")
        do {
            let chips = try await client.fetchChips(at: pointer, selection: selection)
            showStatus("")
            guard !chips.isEmpty else {
                showStatus("No suggestions here.")
                return
            }
            chipsPanel.show(chips, near: point) { [weak self] chip in
                Task { @MainActor in await self?.runChip(chip, at: pointer) }
            }
        } catch {
            showStatus(error.localizedDescription)
        }
    }

    private func runChip(_ chip: Chip, at pointer: CGPoint) async {
        switch chip.kind {
        case "transform":
            await transformPanel.begin(client: client, instruction: chip.prompt) { [weak self] in
                self?.showStatus($0)
            }
        case "execute":
            submitGoal(chip.prompt)
        default:        // understand, ideate
            do {
                try await askTalk(chip.prompt, at: pointer)
            } catch {
                showStatus(error.localizedDescription)
            }
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

            HStack {
                Button("Open Chat (⌃⌘A)") { app.openChat() }
                Spacer()
            }
            DisclosureGroup("About you & things to try") {
                AboutYouView(client: app.client)
            }
            .font(.caption)
            DisclosureGroup("Quick skills (⌃⌥1–9)") {
                QuickSkillsView(controller: app.quickSkills, client: app.client)
            }
            .font(.caption)

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
