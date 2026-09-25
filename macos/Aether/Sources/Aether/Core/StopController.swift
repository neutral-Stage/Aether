import AppKit
import Carbon.HIToolbox

/// STOP from anywhere: ⌃⇧S always; double-Escape while something is running;
/// and triple-tap Control (nothing else held) for always-on listening.
@MainActor
final class StopController: ObservableObject {
    @Published var isStopPressed = false

    /// Whether something is running that double-Escape should stop. Escape is an
    /// ordinary key, so it stops nothing while Aether is idle.
    var isActive: () -> Bool = { false }
    /// Triple-tap Control.
    var onTripleControl: (() -> Void)?

    private var monitors: [Any] = []
    private let onStop: () -> Void
    private var escapes = TapSequence(count: 2, gap: 0.4)
    private var controls = TapSequence(count: 3, gap: 0.35)

    init(onStop: @escaping () -> Void) {
        self.onStop = onStop
    }

    func start() {
        guard monitors.isEmpty else { return }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .keyDown, handler: { [weak self] event in
            self?.handleKey(event)
        }) { monitors.append(m) }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { [weak self] event in
            self?.handleKey(event)
            return event
        }) { monitors.append(m) }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged, handler: { [weak self] event in
            self?.handleFlags(event)
        }) { monitors.append(m) }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged, handler: { [weak self] event in
            self?.handleFlags(event)
            return event
        }) { monitors.append(m) }
    }

    func stop() {
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors.removeAll()
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
        controls.reset()   // a key between Control taps breaks the sequence
        let flags = Self.significant(event.modifierFlags)
        let needs = AetherConfig.stopHotkeyModifiers
        if flags.contains(needs) && event.keyCode == AetherConfig.stopHotkeyKey {
            trigger()
            return
        }
        guard event.keyCode == UInt16(kVK_Escape), flags.isEmpty else {
            escapes.reset()
            return
        }
        if !event.isARepeat, escapes.register(at: event.timestamp), isActive() {
            trigger()
        }
    }

    private func handleFlags(_ event: NSEvent) {
        escapes.reset()
        let isControlKey = event.keyCode == UInt16(kVK_Control)
            || event.keyCode == UInt16(kVK_RightControl)
        let flags = Self.significant(event.modifierFlags)
        guard isControlKey else {
            controls.reset()
            return
        }
        if flags == .control {            // Control went down, nothing else held
            if controls.register(at: event.timestamp) { onTripleControl?() }
        } else if !flags.isEmpty {        // Control with another modifier
            controls.reset()
        }                                  // Control released: keep counting
    }

    private static func significant(_ flags: NSEvent.ModifierFlags) -> NSEvent.ModifierFlags {
        flags.intersection(.deviceIndependentFlagsMask).subtracting([.capsLock, .numericPad, .function])
    }
}
