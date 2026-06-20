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

    /// Capture one PNG frame from the main display to a temp file.
    static func captureFrame() async throws -> URL {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "Aether", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "No display available for capture.",
            ])
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = Int(display.width)
        config.height = Int(display.height)
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = true

        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: config
        )

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("aether-sc-\(UUID().uuidString).png")
        try writePNG(image: image, to: url)
        return url
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
