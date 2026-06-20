import AppKit
import Carbon.HIToolbox

@MainActor
final class StopController: ObservableObject {
    @Published var isStopPressed = false

    private var globalMonitor: Any?
    private var localMonitor: Any?
    private let onStop: () -> Void

    init(onStop: @escaping () -> Void) {
        self.onStop = onStop
    }

    func start() {
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.handleKey(event)
        }
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.handleKey(event)
            return event
        }
    }

    func stop() {
        if let globalMonitor { NSEvent.removeMonitor(globalMonitor) }
        if let localMonitor { NSEvent.removeMonitor(localMonitor) }
        globalMonitor = nil
        localMonitor = nil
    }

    func trigger() {
        guard !isStopPressed else { return }
        isStopPressed = true
        onStop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.isStopPressed = false
        }
    }

    private func handleKey(_ event: NSEvent) {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let needs = AetherConfig.stopHotkeyModifiers
        if flags.contains(needs) && event.keyCode == AetherConfig.stopHotkeyKey {
            trigger()
        }
    }
}
