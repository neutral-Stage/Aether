import Foundation

/// Ambient listening loop — VAD + optional wake word (Phase 7, FR-7).
@MainActor
final class AmbientListeningController: ObservableObject {
    @Published var isActive = false
    @Published var indicatorVisible = false

    private let audio: AudioEngine
    private let wake: WakeWordDetector
    private var monitorTask: Task<Void, Never>?

    var onWake: (() -> Void)?

    init(audio: AudioEngine, wake: WakeWordDetector) {
        self.audio = audio
        self.wake = wake
        wake.onWake = { [weak self] in
            self?.onWake?()
        }
    }

    func start(threshold: Float) {
        guard !isActive else { return }
        isActive = true
        indicatorVisible = true
        wake.isListening = true
        wake.energyThreshold = max(threshold * 1.5, 0.03)
        do {
            try audio.startContinuousMonitoring(threshold: threshold) { [weak self] energy in
                Task { @MainActor in
                    self?.wake.processEnergy(energy)
                }
            }
        } catch {
            isActive = false
            indicatorVisible = false
        }
    }

    func stop() {
        guard isActive else { return }
        isActive = false
        indicatorVisible = false
        wake.isListening = false
        wake.reset()
        audio.stopContinuousMonitoring()
        monitorTask?.cancel()
        monitorTask = nil
    }
}
