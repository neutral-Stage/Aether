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

    private var highEnergyStreak = 0

    func reset() {
        highEnergyStreak = 0
    }

    func processEnergy(_ energy: Float) {
        guard isListening else { return }
        switch engine {
        case .energy:
            processEnergyWake(energy)
        case .porcupine:
            // Porcupine hook — load keyword model and call process(frame) here.
            processEnergyWake(energy)
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
