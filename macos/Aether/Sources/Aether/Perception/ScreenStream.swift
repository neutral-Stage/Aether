import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

/// Low-FPS continuous ScreenCaptureKit stream (Phase 8, FR-4).
@available(macOS 14.0, *)
@MainActor
final class ScreenStreamManager: NSObject, ObservableObject, SCStreamOutput {
    @Published var isStreaming = false
    @Published var frameCount = 0
    @Published var lastError: String?

    private var stream: SCStream?
    private var minInterval: TimeInterval = 2.0
    private var lastFrameAt: Date = .distantPast
    private var onFrame: ((Int, Int, String) -> Void)?

    func start(fps: Double, onFrame: @escaping (Int, Int, String) -> Void) async {
        guard !isStreaming else { return }
        self.onFrame = onFrame
        minInterval = fps > 0 ? 1.0 / fps : 2.0
        lastError = nil

        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: true
            )
            guard let display = content.displays.first else {
                lastError = "No display for SCStream"
                return
            }

            let filter = SCContentFilter(display: display, excludingWindows: [])
            let config = SCStreamConfiguration()
            config.width = min(Int(display.width), 1280)
            config.height = min(Int(display.height), 720)
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.showsCursor = false
            config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(max(1, Int32(fps * 10))))
            config.queueDepth = 2

            let scStream = SCStream(filter: filter, configuration: config, delegate: nil)
            try scStream.addStreamOutput(self, type: .screen, sampleHandlerQueue: .global(qos: .utility))
            try await scStream.startCapture()
            stream = scStream
            isStreaming = true
        } catch {
            lastError = error.localizedDescription
            isStreaming = false
        }
    }

    func stop() async {
        guard let stream else { return }
        try? await stream.stopCapture()
        self.stream = nil
        isStreaming = false
        onFrame = nil
    }

    nonisolated func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .screen else { return }
        Task { @MainActor in
            let now = Date()
            guard now.timeIntervalSince(self.lastFrameAt) >= self.minInterval else { return }
            self.lastFrameAt = now
            guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
            let width = CVPixelBufferGetWidth(imageBuffer)
            let height = CVPixelBufferGetHeight(imageBuffer)
            self.frameCount += 1
            let hash = "\(width)x\(height)-\(self.frameCount)"
            self.onFrame?(width, height, hash)
        }
    }
}
