import AppKit
import QuartzCore

/// Shows where things are: a pointer flies to each target and draws a ring,
/// box or underline around it, with a label.
///
/// One borderless, click-through panel per screen at screen-saver level,
/// joined to all Spaces, excluded from screen capture (so Aether's own
/// screenshots and screen sharing don't show it). Clears itself after
/// `holdSeconds`, on Escape, or when `clear()` is called (STOP).
@MainActor
final class OverlayController {
    var holdSeconds: TimeInterval = 6
    private var panels: [NSPanel] = []
    private var fadeTask: Task<Void, Never>?
    private var keyMonitors: [Any] = []
    private let accent = NSColor.systemPink

    /// `hold` overrides how long the marks stay (guide steps keep them until done).
    func show(targets: [OverlayTarget], hold: TimeInterval? = nil) {
        let targets = Array(targets.prefix(3))
        guard !targets.isEmpty, let primary = NSScreen.screens.first else { return }
        clear()
        let primaryHeight = primary.frame.height
        var byScreen: [(NSScreen, [OverlayTarget])] = []
        for target in targets {
            let ak = OverlayGeometry.appKit(target.center, primaryHeight: primaryHeight)
            let screen = NSScreen.screens.first { $0.frame.contains(ak) } ?? primary
            if let i = byScreen.firstIndex(where: { $0.0 == screen }) {
                byScreen[i].1.append(target)
            } else {
                byScreen.append((screen, [target]))
            }
        }
        var lastDelay: CFTimeInterval = 0
        for (index, (screen, group)) in byScreen.enumerated() {
            let panel = makePanel(for: screen)
            guard let root = panel.contentView?.layer else { continue }
            let toLocal: (CGPoint) -> CGPoint = { p in
                let ak = OverlayGeometry.appKit(p, primaryHeight: primaryHeight)
                return CGPoint(x: ak.x - screen.frame.minX, y: ak.y - screen.frame.minY)
            }
            let delay = draw(group, in: root, screen: screen, toLocal: toLocal,
                             withFlight: index == 0)
            lastDelay = max(lastDelay, delay)
            panel.orderFrontRegardless()
            panels.append(panel)
        }
        scheduleFade(after: lastDelay + (hold ?? holdSeconds))
        installEscape()
    }

    var isShowing: Bool { !panels.isEmpty }

    func clear() {
        fadeTask?.cancel()
        fadeTask = nil
        panels.forEach { $0.orderOut(nil) }
        panels.removeAll()
        keyMonitors.forEach { NSEvent.removeMonitor($0) }
        keyMonitors.removeAll()
    }

    // MARK: - drawing

    private func draw(_ targets: [OverlayTarget], in root: CALayer, screen: NSScreen,
                      toLocal: (CGPoint) -> CGPoint, withFlight: Bool) -> CFTimeInterval {
        let mouse = NSEvent.mouseLocation
        var from = screen.frame.contains(mouse)
            ? CGPoint(x: mouse.x - screen.frame.minX, y: mouse.y - screen.frame.minY)
            : CGPoint(x: screen.frame.width / 2, y: screen.frame.height / 2)
        let pointer = withFlight ? makePointer() : nil
        if let pointer { root.addSublayer(pointer) }
        var delay: CFTimeInterval = 0
        for target in targets {
            let end = toLocal(target.center)
            if let pointer {
                delay = fly(pointer, from: from, to: end, after: delay)
            }
            drawInk(for: target, in: root, toLocal: toLocal, after: delay)
            drawLabel(target.label, near: end, frameHeight: target.frame.height,
                      in: root, after: delay)
            from = end
            delay += 0.8
        }
        return delay
    }

    private func makePanel(for screen: NSScreen) -> NSPanel {
        let panel = NSPanel(contentRect: screen.frame, styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.setFrame(screen.frame, display: false)
        panel.level = .screenSaver
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary,
                                    .ignoresCycle]
        panel.sharingType = .none
        let view = NSView(frame: NSRect(origin: .zero, size: screen.frame.size))
        view.wantsLayer = true
        panel.contentView = view
        return panel
    }

    private func makePointer() -> CAShapeLayer {
        // An arrow cursor with its tip at the layer's anchor.
        let path = CGMutablePath()
        path.move(to: CGPoint(x: 0, y: 0))
        path.addLine(to: CGPoint(x: 0, y: -22))
        path.addLine(to: CGPoint(x: 6, y: -16))
        path.addLine(to: CGPoint(x: 11, y: -26))
        path.addLine(to: CGPoint(x: 15, y: -24))
        path.addLine(to: CGPoint(x: 10, y: -14))
        path.addLine(to: CGPoint(x: 17, y: -14))
        path.closeSubpath()
        let layer = CAShapeLayer()
        layer.path = path
        layer.fillColor = accent.cgColor
        layer.strokeColor = NSColor.white.cgColor
        layer.lineWidth = 1.5
        layer.shadowColor = NSColor.black.cgColor
        layer.shadowOpacity = 0.35
        layer.shadowRadius = 3
        layer.shadowOffset = CGSize(width: 0, height: -1)
        layer.bounds = CGRect(x: 0, y: -26, width: 18, height: 26)
        layer.anchorPoint = CGPoint(x: 0, y: 1)
        return layer
    }

    private func fly(_ pointer: CAShapeLayer, from a: CGPoint, to b: CGPoint,
                     after delay: CFTimeInterval) -> CFTimeInterval {
        let duration = OverlayGeometry.flightDuration(distance: OverlayGeometry.distance(a, b))
        let settle = 0.17
        let pts = OverlayGeometry.flightPath(from: a, to: b)
        let move = CAKeyframeAnimation(keyPath: "position")
        move.values = pts.map { NSValue(point: $0) }
        move.keyTimes = (0 ..< pts.count).map { i -> NSNumber in
            // Flight over `duration`, the last hop (settle) in 170 ms.
            let total = duration + settle
            let t = i < pts.count - 1 ? Double(i) / Double(pts.count - 2) * duration / total : 1.0
            return NSNumber(value: t)
        }
        move.duration = duration + settle
        let swell = CAKeyframeAnimation(keyPath: "transform.scale")
        swell.values = [1.0, 1.3, 1.0]
        swell.keyTimes = [0, 0.5, 1]
        swell.duration = duration
        let group = CAAnimationGroup()
        group.animations = [move, swell]
        group.duration = duration + settle
        group.beginTime = CACurrentMediaTime() + delay
        group.fillMode = .backwards
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        pointer.position = b
        CATransaction.commit()
        pointer.add(group, forKey: "fly-\(delay)")
        return delay + duration + settle
    }

    private func drawInk(for target: OverlayTarget, in root: CALayer,
                         toLocal: (CGPoint) -> CGPoint, after delay: CFTimeInterval) {
        let path = CGMutablePath()
        switch target.kind {
        case .scribble where target.points.count > 1:
            let pts = target.points.map(toLocal)
            path.addLines(between: pts)
        case .rect:
            let r = localRect(target.frame, toLocal: toLocal).insetBy(dx: -6, dy: -6)
            path.addRoundedRect(in: r, cornerWidth: 8, cornerHeight: 8)
        default:
            let r = OverlayGeometry.ringRect(around: localRect(target.frame, toLocal: toLocal))
            path.addEllipse(in: r)
        }
        let ink = CAShapeLayer()
        ink.path = path
        ink.fillColor = nil
        ink.strokeColor = accent.withAlphaComponent(0.9).cgColor
        ink.lineWidth = 3.5
        ink.lineCap = .round
        ink.lineJoin = .round
        ink.strokeEnd = 1
        let draw = CABasicAnimation(keyPath: "strokeEnd")
        draw.fromValue = 0
        draw.toValue = 1
        draw.duration = 0.5
        draw.beginTime = CACurrentMediaTime() + delay
        draw.fillMode = .backwards
        draw.timingFunction = CAMediaTimingFunction(name: .easeOut)
        ink.add(draw, forKey: "draw")
        root.addSublayer(ink)
    }

    private func drawLabel(_ text: String, near p: CGPoint, frameHeight: CGFloat,
                           in root: CALayer, after delay: CFTimeInterval) {
        guard !text.isEmpty else { return }
        let font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        let size = (text as NSString).size(withAttributes: [.font: font])
        let pill = CALayer()
        pill.backgroundColor = NSColor.black.withAlphaComponent(0.78).cgColor
        pill.cornerRadius = 11
        pill.bounds = CGRect(x: 0, y: 0, width: min(size.width + 22, 320), height: 24)
        pill.position = CGPoint(x: p.x, y: p.y + max(frameHeight, 28) / 2 + 26)
        let label = CATextLayer()
        label.string = text
        label.font = font
        label.fontSize = 13
        label.foregroundColor = NSColor.white.cgColor
        label.alignmentMode = .center
        label.truncationMode = .end
        label.contentsScale = NSScreen.main?.backingScaleFactor ?? 2
        label.frame = pill.bounds.insetBy(dx: 10, dy: 4)
        pill.addSublayer(label)
        let appear = CABasicAnimation(keyPath: "opacity")
        appear.fromValue = 0
        appear.toValue = 1
        appear.duration = 0.25
        appear.beginTime = CACurrentMediaTime() + delay
        appear.fillMode = .backwards
        pill.add(appear, forKey: "appear")
        root.addSublayer(pill)
    }

    private func localRect(_ r: CGRect, toLocal: (CGPoint) -> CGPoint) -> CGRect {
        let a = toLocal(CGPoint(x: r.minX, y: r.minY))
        let b = toLocal(CGPoint(x: r.maxX, y: r.maxY))
        return CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x),
                      height: abs(b.y - a.y))
    }

    // MARK: - lifetime

    private func scheduleFade(after seconds: TimeInterval) {
        fadeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(seconds, 0) * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            for panel in self.panels {
                panel.contentView?.layer?.opacity = 0
            }
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            self.clear()
        }
    }

    private func installEscape() {
        let handler: (NSEvent) -> Void = { [weak self] event in
            if event.keyCode == 53 {   // Escape
                Task { @MainActor in self?.clear() }
            }
        }
        if let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown, handler: handler) {
            keyMonitors.append(global)
        }
        if let local = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { event in
            handler(event)
            return event
        }) {
            keyMonitors.append(local)
        }
    }
}
