import AppKit
import CoreGraphics
import SwiftUI

/// One polite hint (POST /hints/check), always shown with its reason.
struct ScreenHint: Equatable {
    let text: String
    let reason: String
    let category: String      // shortcut | fix | next_step | warning
    let confidence: Double

    static func parse(_ o: [String: Any]) -> ScreenHint? {
        guard let text = o["hint"] as? String, let reason = o["reason"] as? String,
              let category = o["category"] as? String, !text.isEmpty, !reason.isEmpty
        else { return nil }
        return ScreenHint(text: text, reason: reason, category: category,
                          confidence: o["confidence"] as? Double ?? 0)
    }

    var symbol: String {
        switch category {
        case "shortcut": return "keyboard"
        case "fix": return "wrench.adjustable"
        case "warning": return "exclamationmark.triangle"
        default: return "arrow.right.circle"
        }
    }

    /// For "No more … tips".
    var kindName: String {
        switch category {
        case "shortcut": return "shortcut"
        case "fix": return "fix"
        case "warning": return "warning"
        default: return "next-step"
        }
    }
}

struct HintView: View {
    let hint: ScreenHint
    var onClose: () -> Void
    var onMute: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: hint.symbol).foregroundStyle(.tint)
                Text(hint.text)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Why: " + hint.reason)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Thanks", action: onClose)
                Button("No more " + hint.kindName + " tips", action: onMute)
                Spacer()
            }
            .controlSize(.small)
        }
        .padding(12)
        .frame(width: 340, alignment: .leading)
        .background(.ultraThickMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

/// A small card in the top-right corner. It never takes focus and closes itself.
@MainActor
final class HintPanel {
    private var panel: NSPanel?
    private var closer: Task<Void, Never>?

    var isShowing: Bool { panel != nil }

    func show(_ hint: ScreenHint, onMute: @escaping () -> Void) {
        hide()
        let view = HintView(hint: hint,
                            onClose: { [weak self] in self?.hide() },
                            onMute: { [weak self] in
                                onMute()
                                self?.hide()
                            })
        let host = NSHostingView(rootView: view)
        let size = host.fittingSize
        let p = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered,
                        defer: false)
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.contentView = host
        if let screen = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: screen.maxX - size.width - 16,
                                     y: screen.maxY - size.height - 16))
        }
        panel = p
        p.orderFrontRegardless()
        closer = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 14_000_000_000)
            guard !Task.isCancelled else { return }
            self?.hide()
        }
    }

    func hide() {
        closer?.cancel()
        closer = nil
        panel?.orderOut(nil)
        panel = nil
    }
}

/// Asks the sidecar for a hint while the user is idle (it decides whether to ask the
/// model). Does nothing unless `hints.enabled` is on in config.yaml.
@MainActor
final class HintController {
    var isBusy: () -> Bool = { false }

    private let client: OrchestratorClient
    private let panel = HintPanel()
    private var loop: Task<Void, Never>?
    private var enabled = false
    private var checkedEnabled = Date.distantPast

    init(client: OrchestratorClient) {
        self.client = client
    }

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.tick()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    func dismiss() {
        panel.hide()
    }

    private func tick() async {
        if Date().timeIntervalSince(checkedEnabled) > 60 {
            enabled = await client.hintsEnabled()
            checkedEnabled = Date()
        }
        guard enabled, !panel.isShowing, !isBusy() else { return }
        let idle = Self.secondsSinceAnyInput()
        guard idle >= 6 else { return }
        let typing = Self.secondsSinceKeyDown() < 2
        guard let hint = await client.checkHint(idle: idle, typing: typing), !isBusy() else { return }
        let client = self.client
        panel.show(hint) {
            Task { await client.muteHints(category: hint.category) }
        }
    }

    static func secondsSinceAnyInput() -> Double {
        // kCGAnyInputEventType
        CGEventSource.secondsSinceLastEventType(.combinedSessionState,
                                                eventType: CGEventType(rawValue: ~0)!)
    }

    static func secondsSinceKeyDown() -> Double {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .keyDown)
    }
}
