import AppKit
import SwiftUI

@MainActor
final class HUDPanel: NSObject {
    private var panel: NSPanel?
    private var hosting: NSHostingView<HUDView>?

    func show(
        world: WorldModel,
        audio: AudioEngine,
        voice: VoicePipeline,
        isRunning: Bool,
        sidecarOK: Bool,
        ambientActive: Bool,
        onStop: @escaping () -> Void
    ) {
        let view = HUDView(
            world: world,
            audio: audio,
            voice: voice,
            isRunning: isRunning,
            sidecarOK: sidecarOK,
            ambientActive: ambientActive,
            onStop: onStop
        )
        if let hosting {
            hosting.rootView = view
            panel?.orderFrontRegardless()
            return
        }
        let host = NSHostingView(rootView: view)
        hosting = host
        let p = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 340, height: 160),
            styleMask: [.nonactivatingPanel, .hudWindow, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.title = "Aether HUD"
        p.contentView = host
        p.isMovableByWindowBackground = true
        p.hidesOnDeactivate = false
        p.backgroundColor = .clear
        p.isOpaque = false
        position(panel: p)
        panel = p
        p.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    func setClickThrough(_ enabled: Bool) {
        panel?.ignoresMouseEvents = enabled
    }

    private func position(panel: NSPanel) {
        guard let screen = NSScreen.main?.visibleFrame else { return }
        let size = panel.frame.size
        let x = screen.maxX - size.width - 24
        let y = screen.maxY - size.height - 24
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }
}
