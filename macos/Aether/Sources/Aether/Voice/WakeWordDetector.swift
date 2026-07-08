import Foundation

/// Wake-word placeholder (Phase 7, FR-1 / FR-7).
///
/// **Current:** energy-based gate — sustained mic energy above threshold triggers wake.
/// **Future:** swap to [Porcupine](https://github.com/Picovoice/porcupine) via
/// ``WakeWordEngine.porcupine`` when `beta.wake_word_engine: porcupine` and an access key
/// are configured. See `docs/VOICE.md`.
enum WakeWordEngine: String {
    case energy
    case porcupine
}

@MainActor
final class WakeWordDetector: ObservableObject {
    @Published var isListening = false
    @Published var lastWakeAt: Date?
    @Published var wakeCount = 0

    var engine: WakeWordEngine = .energy
    var energyThreshold: Float = 0.035
    var sustainedFramesRequired = 8

    var onWake: (() -> Void)?

    /// Real Porcupine backend, lazily created; nil when the library/key are
    /// absent, in which case `.porcupine` mode degrades to the energy heuristic.
    private(set) lazy var porcupine: PorcupineWakeWord? = PorcupineWakeWord()
    private var frameBuffer: [Int16] = []
    private var highEnergyStreak = 0

    /// True only when Porcupine is selected AND actually loaded.
    var porcupineActive: Bool { engine == .porcupine && (porcupine?.isAvailable ?? false) }

    var requiredSampleRate: Int { porcupine?.sampleRate ?? 16000 }

    func reset() {
        highEnergyStreak = 0
        frameBuffer.removeAll(keepingCapacity: true)
    }

    func processEnergy(_ energy: Float) {
        guard isListening else { return }
        // Energy mode, or Porcupine selected but unavailable → energy fallback.
        if engine == .energy || !(porcupine?.isAvailable ?? false) {
            processEnergyWake(energy)
        }
    }

    /// Feed 16 kHz mono Int16 samples when Porcupine is active.
    func processFrame(_ samples: [Int16]) {
        guard isListening, let pv = porcupine, pv.isAvailable, engine == .porcupine
        else { return }
        frameBuffer.append(contentsOf: samples)
        while frameBuffer.count >= pv.frameLength {
            let frame = Array(frameBuffer.prefix(pv.frameLength))
            frameBuffer.removeFirst(pv.frameLength)
            if pv.process(frame) {
                triggerWake()
            }
        }
    }

    func processPartialTranscript(_ text: String) {
        guard isListening else { return }
        let lower = text.lowercased()
        if lower.contains("hey aether") || lower.contains("ok aether") {
            triggerWake()
        }
    }

    private func processEnergyWake(_ energy: Float) {
        if energy >= energyThreshold {
            highEnergyStreak += 1
            if highEnergyStreak >= sustainedFramesRequired {
                triggerWake()
            }
        } else {
            highEnergyStreak = max(0, highEnergyStreak - 1)
        }
    }

    private func triggerWake() {
        highEnergyStreak = 0
        wakeCount += 1
        lastWakeAt = Date()
        onWake?()
    }
}
