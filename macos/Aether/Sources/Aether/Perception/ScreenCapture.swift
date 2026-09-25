import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit

/// ScreenCaptureKit frame capture (Phase 4 — basic single-frame grab).
@available(macOS 14.0, *)
enum ScreenCapture {
    static func isAvailable() -> Bool {
        true
    }

    /// Returns shareable content summary for diagnostics.
    static func contentSummary() async -> String {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let displays = content.displays.count
            let apps = content.applications.count
            return "ScreenCaptureKit: \(displays) display(s), \(apps) app(s)"
        } catch {
            return "ScreenCaptureKit unavailable: \(error.localizedDescription)"
        }
    }

    struct CaptureResult {
        let url: URL
        let width: Int
        let height: Int
        let displayID: CGDirectDisplayID
        /// Backing pixels per point of the display.
        let scale: Double
        /// Display bounds in global top-left points (CGDisplayBounds).
        let frame: CGRect
    }

    /// Pixel size for a capture of a display, long edge capped at `maxEdge`
    /// (0 = native). ScreenCaptureKit renders at exactly this size, so the
    /// image never needs a second, lossy resize.
    static func targetPixelSize(pointWidth: Double, pointHeight: Double,
                                scale: Double, maxEdge: Int) -> (width: Int, height: Int) {
        var w = max(1.0, (pointWidth * scale).rounded())
        var h = max(1.0, (pointHeight * scale).rounded())
        if maxEdge > 0 {
            let longest = max(w, h)
            if longest > Double(maxEdge) {
                let ratio = Double(maxEdge) / longest
                w = max(1.0, (w * ratio).rounded())
                h = max(1.0, (h * ratio).rounded())
            }
        }
        return (Int(w), Int(h))
    }

    /// Capture one display to a temp PNG with Aether's own windows (HUD,
    /// overlay, command bar) excluded, so models never see Aether's UI.
    static func capture(displayID: CGDirectDisplayID?, maxEdge: Int) async throws -> CaptureResult {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let target = displayID ?? CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == target })
            ?? content.displays.first else {
            throw NSError(domain: "Aether", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "No display available for capture.",
            ])
        }
        let ownPID = ProcessInfo.processInfo.processIdentifier
        let ownApps = content.applications.filter { $0.processID == ownPID }
        let filter = SCContentFilter(display: display, excludingApplications: ownApps, exceptingWindows: [])
        let scale = Double(filter.pointPixelScale)
        let size = targetPixelSize(pointWidth: Double(display.width), pointHeight: Double(display.height),
                                   scale: scale, maxEdge: maxEdge)
        let config = SCStreamConfiguration()
        config.width = size.width
        config.height = size.height
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = false

        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("aether-sc-\(UUID().uuidString).png")
        try writePNG(image: image, to: url)
        return CaptureResult(url: url, width: image.width, height: image.height,
                             displayID: display.displayID, scale: scale,
                             frame: CGDisplayBounds(display.displayID))
    }

    /// Capture one PNG frame from the main display to a temp file.
    static func captureFrame() async throws -> URL {
        try await capture(displayID: nil, maxEdge: 0).url
    }

    private static func writePNG(image: CGImage, to url: URL) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
            throw NSError(domain: "Aether", code: 3, userInfo: [
                NSLocalizedDescriptionKey: "Failed to create PNG destination.",
            ])
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else {
            throw NSError(domain: "Aether", code: 4, userInfo: [
                NSLocalizedDescriptionKey: "Failed to write PNG.",
            ])
        }
    }
}
