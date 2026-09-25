import AppKit
import SwiftUI

/// One suggested next action (POST /chips).
struct Chip: Identifiable, Equatable {
    var id: String { label + kind }
    let label: String
    let kind: String      // understand | transform | ideate | execute
    let prompt: String

    static func parse(_ o: [String: Any]) -> Chip? {
        guard let label = o["label"] as? String, let kind = o["kind"] as? String,
              let prompt = o["prompt"] as? String, !label.isEmpty, !prompt.isEmpty
        else { return nil }
        return Chip(label: label, kind: kind, prompt: prompt)
    }

    var symbol: String {
        switch kind {
        case "understand": return "questionmark.circle"
        case "transform": return "wand.and.stars"
        case "ideate": return "lightbulb"
        default: return "play.circle"
        }
    }
}

/// Up to three chips next to the pointer. Nothing runs until one is clicked; Escape,
/// clicking elsewhere, or 12 seconds closes them.
@MainActor
final class ChipsPanel {
    private var panel: NSPanel?
    private var closer: Task<Void, Never>?
    private var monitor: Any?

    func show(_ chips: [Chip], near point: NSPoint, onPick: @escaping (Chip) -> Void) {
        hide()
        let view = HStack(spacing: 6) {
            ForEach(chips.prefix(3)) { chip in
                Button { [weak self] in
                    self?.hide()
                    onPick(chip)
                } label: {
                    Label(chip.label, systemImage: chip.symbol).font(.callout)
                }
                .buttonStyle(.borderedProminent)
                .help(chip.prompt)
            }
        }
        .padding(8)
        .background(.ultraThickMaterial, in: Capsule())
        let host = NSHostingView(rootView: view)
        let size = host.fittingSize
        let p = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered,
                        defer: false)
        p.isFloatingPanel = true
        p.level = .popUpMenu
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.contentView = host
        p.setFrameOrigin(NSPoint(x: point.x + 12, y: point.y - size.height - 12))
        panel = p
        p.orderFrontRegardless()
        monitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .keyDown]) {
            [weak self] event in
            if event.type == .keyDown && event.keyCode != 53 { return }   // only Escape
            Task { @MainActor in self?.hide() }
        }
        closer = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 12_000_000_000)
            guard !Task.isCancelled else { return }
            self?.hide()
        }
    }

    func hide() {
        closer?.cancel()
        closer = nil
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        panel?.orderOut(nil)
        panel = nil
    }
}
