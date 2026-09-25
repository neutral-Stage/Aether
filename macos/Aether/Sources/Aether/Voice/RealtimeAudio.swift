import AVFoundation
import Foundation

/// Plays streamed 24 kHz mono PCM16 (Realtime API audio) and knows how much of the
/// current reply the user has heard, so an interruption can truncate it there.
@MainActor
final class PCMStreamPlayer {
    static let sampleRate: Double = 24_000

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24_000,
                                       channels: 1, interleaved: false)!
    private var started = false
    private var scheduledFrames: AVAudioFramePosition = 0
    private var itemStartFrame: AVAudioFramePosition = 0
    private(set) var itemId: String?

    init() {
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
    }

    /// Frames played since playback started (never past what was scheduled).
    private var playedFrames: AVAudioFramePosition {
        guard started, let nodeTime = node.lastRenderTime,
              let playerTime = node.playerTime(forNodeTime: nodeTime) else { return 0 }
        return min(max(0, playerTime.sampleTime), scheduledFrames)
    }

    var isPlaying: Bool { started && playedFrames < scheduledFrames }

    /// Milliseconds of the current reply that have been played.
    var heardMs: Int {
        Int(Double(max(0, playedFrames - itemStartFrame)) / Self.sampleRate * 1000)
    }

    func append(pcm16: Data, itemId: String?) {
        let count = pcm16.count / 2
        guard count > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count))
        else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        let out = buffer.floatChannelData![0]
        pcm16.withUnsafeBytes { raw in
            for i in 0 ..< count {
                let sample = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))
                out[i] = Float(sample) / 32768.0
            }
        }
        if itemId != self.itemId {
            self.itemId = itemId
            itemStartFrame = scheduledFrames
        }
        if !started {
            do {
                try engine.start()
            } catch {
                return
            }
            node.play()
            started = true
        }
        node.scheduleBuffer(buffer, completionHandler: nil)
        scheduledFrames += AVAudioFramePosition(count)
    }

    func stop() {
        node.stop()
        engine.stop()
        started = false
        scheduledFrames = 0
        itemStartFrame = 0
        itemId = nil
    }
}

/// Microphone → 24 kHz mono PCM16 chunks for the Realtime API. Runs only while
/// push-to-talk is held; nothing is streamed otherwise. Subscribes to the shared
/// mic hub, so it doesn't open its own `AVAudioEngine`.
@MainActor
final class RealtimeMicStreamer {
    var onChunk: ((Data) -> Void)?
    private(set) var isRunning = false

    private let hub: MicHub
    private let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24_000,
                                       channels: 1, interleaved: true)!
    private var subscription: MicSubscription?

    init(hub: MicHub = .shared) {
        self.hub = hub
    }

    func start() throws {
        guard !isRunning else { return }
        guard let converter = AVAudioConverter(from: hub.currentFormat, to: target) else {
            throw NSError(domain: "Aether", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Can't convert microphone audio"])
        }
        let target = self.target
        subscription = try hub.subscribe { [weak self] buffer in
            guard let data = RealtimeMicStreamer.convert(buffer, with: converter, to: target) else {
                return
            }
            Task { @MainActor in self?.onChunk?(data) }
        }
        isRunning = true
    }

    func stop() {
        guard isRunning else { return }
        subscription?.cancel()
        subscription = nil
        isRunning = false
    }

    nonisolated static func convert(_ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter,
                                    to target: AVAudioFormat) -> Data? {
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed {
                status.pointee = .noDataNow
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return nil }
        return Data(bytes: channel[0], count: Int(out.frameLength) * MemoryLayout<Int16>.size)
    }
}
