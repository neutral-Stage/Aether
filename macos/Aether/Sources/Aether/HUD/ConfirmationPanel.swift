import AppKit
import SwiftUI

struct ConfirmationView: View {
    let description: String
    let onApprove: () -> Void
    let onDecline: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Confirm action", systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
                .foregroundStyle(.orange)
            Text(description)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
            Text("Say “yes” / “no” or use the buttons below.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("No", role: .cancel, action: onDecline)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Yes", role: .destructive, action: onApprove)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 360)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.orange.opacity(0.4), lineWidth: 1)
        )
    }
}

@MainActor
final class ConfirmationPanel: NSObject {
    private var panel: NSPanel?
    private var hosting: NSHostingView<ConfirmationView>?

    func show(
        description: String,
        onApprove: @escaping () -> Void,
        onDecline: @escaping () -> Void
    ) {
        hide()
        let view = ConfirmationView(
            description: description,
            onApprove: { [weak self] in
                onApprove()
                self?.hide()
            },
            onDecline: { [weak self] in
                onDecline()
                self?.hide()
            }
        )
        let host = NSHostingView(rootView: view)
        hosting = host
        let p = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 180),
            styleMask: [.nonactivatingPanel, .hudWindow],
            backing: .buffered,
            defer: false
        )
        p.isFloatingPanel = true
        p.level = .floating + 1
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.title = "Aether Confirm"
        p.contentView = host
        p.backgroundColor = .clear
        p.isOpaque = false
        position(panel: p)
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
        hosting = nil
    }

    private func position(panel: NSPanel) {
        guard let screen = NSScreen.main?.visibleFrame else { return }
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(
            x: screen.midX - size.width / 2,
            y: screen.midY - size.height / 2
        ))
    }
}
