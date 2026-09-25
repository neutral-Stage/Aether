import CoreGraphics
import Foundation

/// A pointing target from the sidecar (talk replies and agent `pointer` events),
/// in global top-left screen points, the space Quartz and Accessibility use.
struct OverlayTarget: Equatable {
    enum Kind: String { case point, rect, scribble }

    var kind: Kind
    var center: CGPoint
    var size: CGSize
    var label: String
    var points: [CGPoint]

    init(kind: Kind, center: CGPoint, size: CGSize = .zero, label: String = "",
         points: [CGPoint] = []) {
        self.kind = kind
        self.center = center
        self.size = size
        self.label = label
        self.points = points
    }

    init?(json: [String: Any]) {
        guard let x = (json["x"] as? NSNumber)?.doubleValue,
              let y = (json["y"] as? NSNumber)?.doubleValue else { return nil }
        kind = Kind(rawValue: json["kind"] as? String ?? "point") ?? .point
        center = CGPoint(x: x, y: y)
        size = CGSize(width: (json["w"] as? NSNumber)?.doubleValue ?? 0,
                      height: (json["h"] as? NSNumber)?.doubleValue ?? 0)
        label = json["label"] as? String ?? ""
        points = (json["points"] as? [[Any]] ?? []).compactMap { pair in
            guard pair.count == 2,
                  let px = (pair[0] as? NSNumber)?.doubleValue,
                  let py = (pair[1] as? NSNumber)?.doubleValue else { return nil }
            return CGPoint(x: px, y: py)
        }
    }

    /// The thing's frame; a small box around bare points.
    var frame: CGRect {
        let w = max(size.width, 0), h = max(size.height, 0)
        if w > 0, h > 0 {
            return CGRect(x: center.x - w / 2, y: center.y - h / 2, width: w, height: h)
        }
        return CGRect(x: center.x - 14, y: center.y - 14, width: 28, height: 28)
    }
}

/// Pure geometry for the pointer overlay (Clicky's flight, avatar-cursor's
/// overshoot). Kept free of AppKit so it is unit-tested.
enum OverlayGeometry {
    /// Global top-left point → AppKit global point (origin bottom-left of the primary screen).
    static func appKit(_ p: CGPoint, primaryHeight: CGFloat) -> CGPoint {
        CGPoint(x: p.x, y: primaryHeight - p.y)
    }

    static func appKitRect(_ r: CGRect, primaryHeight: CGFloat) -> CGRect {
        CGRect(x: r.minX, y: primaryHeight - r.maxY, width: r.width, height: r.height)
    }

    /// AppKit global point → global top-left point.
    static func topLeft(_ p: CGPoint, primaryHeight: CGFloat) -> CGPoint {
        CGPoint(x: p.x, y: primaryHeight - p.y)
    }

    static func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
        hypot(b.x - a.x, b.y - a.y)
    }

    /// Flight time grows with distance: 0.6 s to 1.4 s.
    static func flightDuration(distance d: CGFloat) -> TimeInterval {
        min(max(Double(d) / 800.0, 0.6), 1.4)
    }

    /// Control point of the quadratic curve: the midpoint lifted sideways by
    /// min(20% of the distance, 80 pt), bowing upward on screen.
    static func controlPoint(from a: CGPoint, to b: CGPoint) -> CGPoint {
        let mid = CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2)
        let d = distance(a, b)
        guard d > 0 else { return mid }
        let lift = min(d * 0.2, 80)
        var nx = -(b.y - a.y) / d
        var ny = (b.x - a.x) / d
        if ny < 0 { nx = -nx; ny = -ny }   // AppKit y grows upward: bow up
        return CGPoint(x: mid.x + nx * lift, y: mid.y + ny * lift)
    }

    static func smoothstep(_ t: Double) -> Double {
        let c = min(max(t, 0), 1)
        return c * c * (3 - 2 * c)
    }

    static func bezier(_ a: CGPoint, _ c: CGPoint, _ b: CGPoint, _ t: CGFloat) -> CGPoint {
        let u = 1 - t
        return CGPoint(x: u * u * a.x + 2 * u * t * c.x + t * t * b.x,
                       y: u * u * a.y + 2 * u * t * c.y + t * t * b.y)
    }

    /// Size of the pointer mid-flight: swells to 1.3× halfway.
    static func swell(_ t: Double) -> CGFloat {
        1 + 0.3 * CGFloat(sin(Double.pi * min(max(t, 0), 1)))
    }

    /// Where the pointer overshoots before settling: past the target along the
    /// direction of travel, by min(12 pt, 6% of the distance).
    static func overshoot(from a: CGPoint, to b: CGPoint) -> CGPoint {
        let d = distance(a, b)
        guard d > 0 else { return b }
        let o = min(12, d * 0.06)
        return CGPoint(x: b.x + (b.x - a.x) / d * o, y: b.y + (b.y - a.y) / d * o)
    }

    /// Sampled flight: eased along the curve to the overshoot point, then back.
    static func flightPath(from a: CGPoint, to b: CGPoint, samples: Int = 30) -> [CGPoint] {
        let over = overshoot(from: a, to: b)
        let c = controlPoint(from: a, to: over)
        let n = max(samples, 2)
        var pts = (0 ... n).map { i -> CGPoint in
            bezier(a, c, over, CGFloat(smoothstep(Double(i) / Double(n))))
        }
        pts.append(b)
        return pts
    }

    /// The ring drawn around a target: padded, and never smaller than minSize.
    static func ringRect(around frame: CGRect, padding: CGFloat = 8,
                         minSize: CGFloat = 36) -> CGRect {
        var r = frame.insetBy(dx: -padding, dy: -padding)
        if r.width < minSize { r = r.insetBy(dx: -(minSize - r.width) / 2, dy: 0) }
        if r.height < minSize { r = r.insetBy(dx: 0, dy: -(minSize - r.height) / 2) }
        return r
    }

    /// Index of the frame (top-left global) that holds the point.
    static func screenIndex(for p: CGPoint, frames: [CGRect]) -> Int? {
        frames.firstIndex { $0.contains(p) }
    }
}
