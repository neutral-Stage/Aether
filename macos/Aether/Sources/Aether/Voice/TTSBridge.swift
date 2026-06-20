import AVFoundation
import Foundation

struct VoiceSettings: Equatable {
    var stt: String = "groq"
    var sttModel: String = "whisper-large-v3-turbo"
    var tts: String = "groq"
    var ttsModel: String = "canopylabs/orpheus-v1-english"
    var ttsVoice: String = "troy"
    var ttsStream: Bool = true
    var mode: String = "pipeline"
    var realtimeProvider: String = "openai"
    var realtimeVoice: Bool = false
    var bargeIn: Bool = true
    var vadEnergyThreshold: Float = 0.02

    var prefersGroqSTT: Bool { stt == "groq" }
    var prefersGroqTTS: Bool { tts == "groq" }
    var prefersStreamingTTS: Bool { ttsStream }
    var usesRealtimeMode: Bool { mode == "realtime" && realtimeVoice }
}

/// Low-latency TTS via AVSpeechSynthesizer with barge-in duck/stop (§6.1).
@MainActor
final class TTSBridge: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published var isSpeaking = false

    private let synthesizer = AVSpeechSynthesizer()
    private var finishContinuation: CheckedContinuation<Void, Never>?
    private var baseVolume: Float = 1.0
    private var currentUtterance: AVSpeechUtterance?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String, rate: Float = AVSpeechUtteranceDefaultSpeechRate, volume: Float = 1.0) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        stop()
        baseVolume = volume
        isSpeaking = true

        if voiceSettings.prefersGroqTTS, let synthesize = groqSynthesize {
            if voiceSettings.prefersStreamingTTS, let stream = groqSynthesizeStream {
                let streamed = await speakGroqStream(trimmed, stream: stream)
                if streamed { return }
            }
            await speakGroq(trimmed, synthesize: synthesize)
            return
        }

        let utterance = AVSpeechUtterance(string: trimmed)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = rate
        utterance.volume = volume
        currentUtterance = utterance
        await withCheckedContinuation { cont in
            finishContinuation = cont
            synthesizer.speak(utterance)
        }
    }

    /// When set, Groq TTS audio is fetched from the sidecar `/tts` endpoint.
    var groqSynthesize: ((String) async throws -> Data)?
    /// Streaming TTS via sidecar `POST /tts/stream` (Phase 10).
    var groqSynthesizeStream: ((String) async throws -> Data)?
    var voiceSettings = VoiceSettings()

    private var audioPlayer: AVAudioPlayer?
    private var streamEngine: AVAudioEngine?
    private var streamPlayer: AVAudioPlayerNode?

    private func speakGroqStream(
        _ text: String,
        stream: (String) async throws -> Data
    ) async -> Bool {
        do {
            let data = try await stream(text)
            guard !data.isEmpty else {
                isSpeaking = false
                return false
            }
            let player = try AVAudioPlayer(data: data)
            player.prepareToPlay()
            audioPlayer = player
            await withCheckedContinuation { cont in
                finishContinuation = cont
                player.play()
                let duration = player.duration
                DispatchQueue.main.asyncAfter(deadline: .now() + max(duration, 0.1)) {
                    Task { @MainActor in
                        self.isSpeaking = false
                        self.audioPlayer = nil
                        self.finishContinuation?.resume()
                        self.finishContinuation = nil
                    }
                }
            }
            return true
        } catch {
            print("[TTS stream error: \(error)] falling back to /tts")
            return false
        }
    }

    private func speakGroq(_ text: String, synthesize: (String) async throws -> Data) async {
        do {
            let data = try await synthesize(text)
            guard !data.isEmpty else {
                isSpeaking = false
                return
            }
            let player = try AVAudioPlayer(data: data)
            player.prepareToPlay()
            audioPlayer = player
            await withCheckedContinuation { cont in
                finishContinuation = cont
                player.play()
                let duration = player.duration
                DispatchQueue.main.asyncAfter(deadline: .now() + max(duration, 0.1)) {
                    Task { @MainActor in
                        self.isSpeaking = false
                        self.audioPlayer = nil
                        self.finishContinuation?.resume()
                        self.finishContinuation = nil
                    }
                }
            }
        } catch {
            isSpeaking = false
            print("[TTS groq error: \(error)] \(text)")
        }
    }

    func duckVolume(to level: Float) {
        currentUtterance?.volume = level
    }

    func restoreVolume() {
        currentUtterance?.volume = baseVolume
    }

    func stop() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        audioPlayer?.stop()
        audioPlayer = nil
        streamPlayer?.stop()
        streamEngine?.stop()
        streamEngine = nil
        streamPlayer = nil
        isSpeaking = false
        currentUtterance = nil
        finishContinuation?.resume()
        finishContinuation = nil
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isSpeaking = false
            self.finishContinuation?.resume()
            self.finishContinuation = nil
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isSpeaking = false
            self.finishContinuation?.resume()
            self.finishContinuation = nil
        }
    }
}
