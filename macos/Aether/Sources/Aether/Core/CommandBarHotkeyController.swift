import AppKit

/// Global command bar hotkey — ⌥Space (Phase 7, FR-1).
@MainActor
final class CommandBarHotkeyController {
    var onToggle: (() -> Void)?

    private var globalKeyDown: Any?
    private var localKeyDown: Any?

    func start() {
        stop()
        globalKeyDown = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] in
            self?.handle($0)
        }
        localKeyDown = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.handle(event)
            return event
        }
    }

    func stop() {
        if let globalKeyDown { NSEvent.removeMonitor(globalKeyDown) }
        if let localKeyDown { NSEvent.removeMonitor(localKeyDown) }
        globalKeyDown = nil
        localKeyDown = nil
    }

    private func handle(_ event: NSEvent) {
        guard event.type == .keyDown, !event.isARepeat else { return }
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard flags.contains(AetherConfig.commandBarModifiers) else { return }
        let stripped = flags.subtracting([.capsLock, .numericPad, .function])
        let needs = AetherConfig.commandBarModifiers
        guard stripped == needs || stripped == needs.union(.function) else { return }
        guard event.keyCode == AetherConfig.commandBarKeyCode else { return }
        onToggle?()
    }
}
