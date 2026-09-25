import AppKit

/// A global key chord: ⌥Space for the command bar (Phase 7, FR-1), ⌃⌘A for the chat window.
@MainActor
final class CommandBarHotkeyController {
    var onToggle: (() -> Void)?
    private let modifiers: NSEvent.ModifierFlags
    private let keyCode: UInt16

    init(modifiers: NSEvent.ModifierFlags = AetherConfig.commandBarModifiers,
         keyCode: UInt16 = AetherConfig.commandBarKeyCode) {
        self.modifiers = modifiers
        self.keyCode = keyCode
    }

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
        guard flags.contains(modifiers) else { return }
        let stripped = flags.subtracting([.capsLock, .numericPad, .function])
        guard stripped == modifiers || stripped == modifiers.union(.function) else { return }
        guard event.keyCode == keyCode else { return }
        onToggle?()
    }
}
