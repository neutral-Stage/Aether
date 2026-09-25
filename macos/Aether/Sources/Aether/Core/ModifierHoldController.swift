import AppKit

/// Hold-to-talk on a modifier chord (default ⌃⌥), Clicky style.
///
/// Holding exactly the chord for `holdDelay` begins talk mode and reports where
/// the mouse was (global top-left points), so "this" means what the user was
/// pointing at. Releasing ends it. Pressing any other key while the chord is
/// down cancels, so ordinary ⌃⌥ shortcuts keep working. Listen-only: events are
/// observed, never swallowed.
@MainActor
final class ModifierHoldController {
    var onBegin: ((CGPoint) -> Void)?
    var onEnd: (() -> Void)?
    var onCancel: (() -> Void)?

    private let required: NSEvent.ModifierFlags
    private let holdDelay: TimeInterval
    private var monitors: [Any] = []
    private var pending: Task<Void, Never>?
    private(set) var isHeld = false

    init(modifiers: NSEvent.ModifierFlags = AetherConfig.talkModifiers, holdDelay: TimeInterval = 0.25) {
        required = modifiers
        self.holdDelay = holdDelay
    }

    /// True when exactly `required` is down (caps lock, fn and keypad ignored).
    nonisolated static func isExactly(_ flags: NSEvent.ModifierFlags,
                                      _ required: NSEvent.ModifierFlags) -> Bool {
        flags.intersection(.deviceIndependentFlagsMask)
            .subtracting([.capsLock, .numericPad, .function]) == required
    }

    /// The mouse position in global top-left points (the sidecar's space).
    static func mouseTopLeft() -> CGPoint {
        let p = NSEvent.mouseLocation
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        return OverlayGeometry.topLeft(p, primaryHeight: primaryHeight)
    }

    func start() {
        stop()
        let flags: (NSEvent) -> Void = { [weak self] event in
            Task { @MainActor in self?.handleFlags(event.modifierFlags) }
        }
        let key: (NSEvent) -> Void = { [weak self] _ in
            Task { @MainActor in self?.handleOtherKey() }
        }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged, handler: flags) {
            monitors.append(m)
        }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .keyDown, handler: key) {
            monitors.append(m)
        }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged, handler: { event in
            flags(event)
            return event
        }) {
            monitors.append(m)
        }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { event in
            key(event)
            return event
        }) {
            monitors.append(m)
        }
    }

    func stop() {
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors.removeAll()
        pending?.cancel()
        pending = nil
        isHeld = false
    }

    private func handleFlags(_ flags: NSEvent.ModifierFlags) {
        if Self.isExactly(flags, required) {
            guard !isHeld, pending == nil else { return }
            let point = Self.mouseTopLeft()
            pending = Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64((self?.holdDelay ?? 0.25) * 1_000_000_000))
                guard let self, !Task.isCancelled else { return }
                self.pending = nil
                self.isHeld = true
                self.onBegin?(point)
            }
        } else {
            pending?.cancel()
            pending = nil
            if isHeld {
                isHeld = false
                onEnd?()
            }
        }
    }

    private func handleOtherKey() {
        pending?.cancel()
        pending = nil
        if isHeld {
            isHeld = false
            onCancel?()
        }
    }
}
