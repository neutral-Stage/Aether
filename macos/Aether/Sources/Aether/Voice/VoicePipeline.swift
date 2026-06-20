import AVFoundation
import Foundation

/// Full-duplex voice: mic stays open during TTS; barge-in stops speech (§6.1, FR-3).
@MainActor
final class VoicePipeline: ObservableObject {
    @Published var partialTranscript = ""
    @Published var isListeningDuringTTS = false
    @Published var bargeInCount = 0

    private let audio: AudioEngine
    private let stt: STTBridge
    private let tts: TTSBridge

    private var monitorTask: Task<Void, Never>?
    private var onBargeIn: (() -> Void)?
    private var energyThreshold: Float = 0.02

    init(audio: AudioEngine, stt: STTBridge, tts: TTSBridge) {
        self.audio = audio
        self.stt = stt
        self.tts = tts
    }

    func configure(energyThreshold: Float = 0.02) {
        self.energyThreshold = energyThreshold
    }

    /// Speak with barge-in monitoring — mic tap stays active; user speech stops TTS.
    func speakWithBargeIn(_ text: String, onBargeIn: @escaping () -> Void) async {
        self.onBargeIn = onBargeIn
        partialTranscript = ""
        isListeningDuringTTS = true
        audio.setMicGated(true)
        audio.configureGate(multiplier: 2.5)

        startBargeInMonitor()

        let ttsStart = Date()
        await tts.speak(text, volume: 0.85)
        let ttsMs = Date().timeIntervalSince(ttsStart) * 1000

        stopBargeInMonitor()
        audio.setMicGated(false)
        isListeningDuringTTS = false
        tts.restoreVolume()
        onMetrics?(nil, ttsMs, nil)
    }

    /// Optional callback for voice metrics reporting (wired to sidecar in AppState).
    var onMetrics: ((_ sttMs: Double?, _ ttsMs: Double?, _ voiceRttMs: Double?) -> Void)?

    func stopAll() {
        stopBargeInMonitor()
        tts.stop()
        audio.stopContinuousMonitoring()
        isListeningDuringTTS = false
    }

    private func startBargeInMonitor() {
        stopBargeInMonitor()
        do {
            try audio.startContinuousMonitoring(threshold: energyThreshold) { [weak self] energy in
                guard let self else { return }
                Task { @MainActor in
                    self.handleMicEnergy(energy)
                }
            }
        } catch {
            return
        }

        monitorTask = Task { @MainActor in
            await stt.startPartialRecognition { [weak self] partial in
                Task { @MainActor in
                    guard let self else { return }
                    self.partialTranscript = partial
                    if partial.count >= 2 {
                        self.triggerBargeIn()
                    }
                }
            }
        }
    }

    private func stopBargeInMonitor() {
        monitorTask?.cancel()
        monitorTask = nil
        stt.stopPartialRecognition()
        audio.stopContinuousMonitoring()
    }

    private func handleMicEnergy(_ energy: Float) {
        guard tts.isSpeaking else { return }
        if energy > energyThreshold * 1.5 {
            tts.duckVolume(to: 0.15)
        }
        if energy > energyThreshold * 3.0 {
            triggerBargeIn()
        }
    }

    private func triggerBargeIn() {
        guard tts.isSpeaking else { return }
        audio.setMicGated(false)
        bargeInCount += 1
        tts.stop()
        onBargeIn?()
    }
}
