import AppKit
import Carbon.HIToolbox

/// Global push-to-talk hotkey (Phase 5).
/// Hold the configured modifier+key to record; release to transcribe and send.
@MainActor
final class PTTHotkeyController {
    var onBegin: (() -> Void)?
    var onEnd: (() -> Void)?

    private var globalKeyDown: Any?
    private var localKeyDown: Any?
    private var isHeld = false

    func start() {
        stop()
        let mask: NSEvent.EventTypeMask = [.keyDown, .keyUp]
        globalKeyDown = NSEvent.addGlobalMonitorForEvents(matching: mask) { [weak self] in
            self?.handle($0)
        }
        localKeyDown = NSEvent.addLocalMonitorForEvents(matching: mask) { [weak self] event in
            self?.handle(event)
            return event
        }
    }

    func stop() {
        if let globalKeyDown { NSEvent.removeMonitor(globalKeyDown) }
        if let localKeyDown { NSEvent.removeMonitor(localKeyDown) }
        globalKeyDown = nil
        localKeyDown = nil
        isHeld = false
    }

    private func handle(_ event: NSEvent) {
        guard matchesPTT(event) else { return }
        switch event.type {
        case .keyDown where !event.isARepeat:
            guard !isHeld else { return }
            isHeld = true
            onBegin?()
        case .keyUp:
            guard isHeld else { return }
            isHeld = false
            onEnd?()
        default:
            break
        }
    }

    private func matchesPTT(_ event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let needs = AetherConfig.pttModifiers
        guard flags.contains(needs) else { return false }
        // Require only PTT modifiers (no extra shift/command clutter)
        let stripped = flags.subtracting([.capsLock, .numericPad, .function])
        guard stripped == needs || stripped == needs.union(.function) else { return false }
        return event.keyCode == AetherConfig.pttKeyCode
    }
}
