import AppKit
import SwiftUI

@MainActor
final class CommandBarPanel: NSObject {
    private var panel: NSPanel?
    private var hosting: NSHostingView<CommandBarView>?
    private var goalBinding = ""

    func toggle(
        goalText: Binding<String>,
        isRunning: Bool,
        onSubmit: @escaping (String) -> Void
    ) {
        if panel?.isVisible == true {
            hide()
            return
        }
        show(goalText: goalText, isRunning: isRunning, onSubmit: onSubmit)
    }

    func show(
        goalText: Binding<String>,
        isRunning: Bool,
        onSubmit: @escaping (String) -> Void
    ) {
        let view = CommandBarView(
            goalText: goalText,
            isRunning: isRunning,
            onSubmit: onSubmit,
            onDismiss: { [weak self] in self?.hide() }
        )
        if let hosting {
            hosting.rootView = view
            panel?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let host = NSHostingView(rootView: view)
        hosting = host
        let p = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 540, height: 56),
            styleMask: [.nonactivatingPanel, .hudWindow, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.title = "Aether Command"
        p.contentView = host
        p.isMovableByWindowBackground = false
        p.backgroundColor = .clear
        p.isOpaque = false
        p.becomesKeyOnlyIfNeeded = false
        position(panel: p)
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func position(panel: NSPanel) {
        guard let screen = NSScreen.main?.visibleFrame else { return }
        let size = panel.frame.size
        let x = screen.midX - size.width / 2
        let y = screen.maxY - size.height - 120
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }
}
