@preconcurrency import AVFoundation
import Foundation

/// The engine side of shared microphone capture, kept behind a protocol so
/// `MicHub`'s subscriber bookkeeping can be tested without a real `AVAudioEngine`.
protocol MicInput: AnyObject {
    /// The input's current format (the hardware rate while running).
    var format: AVAudioFormat { get }
    var isRunning: Bool { get }
    func installTap(_ block: @escaping (AVAudioPCMBuffer, AVAudioTime) -> Void)
    func removeTap()
    func start() throws
    func stop()
}

/// Wraps one `AVAudioEngine`'s input node. The only place in Aether that opens
/// a microphone-facing `AVAudioEngine` — everything else goes through `MicHub`.
final class SystemMicInput: MicInput {
    let engine = AVAudioEngine()

    var format: AVAudioFormat { engine.inputNode.outputFormat(forBus: 0) }
    var isRunning: Bool { engine.isRunning }

    func installTap(_ block: @escaping (AVAudioPCMBuffer, AVAudioTime) -> Void) {
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: format, block: block)
    }

    func removeTap() {
        engine.inputNode.removeTap(onBus: 0)
    }

    func start() throws {
        engine.prepare()
        try engine.start()
    }

    func stop() {
        engine.stop()
    }
}

/// One handle on the shared microphone. Cancel it (or let it deinit) when you no
/// longer need audio; once the last subscription is gone, the mic is released.
final class MicSubscription {
    private let id: UUID
    private weak var hub: MicHub?
    private let lock = NSLock()
    private var cancelled = false

    fileprivate init(id: UUID, hub: MicHub) {
        self.id = id
        self.hub = hub
    }

    /// Idempotent — a second call (or the one from `deinit`) is a no-op.
    func cancel() {
        lock.lock()
        let already = cancelled
        cancelled = true
        lock.unlock()
        guard !already else { return }
        hub?.unsubscribe(id)
    }

    deinit {
        cancel()
    }
}

/// One shared microphone capture for the whole app. Every feature that needs the
/// mic — recording, wake word, barge-in, realtime streaming, meeting notes — gets
/// its audio from here instead of opening its own `AVAudioEngine`, so there is
/// only ever one tap on the input node. The mic starts on the first subscriber
/// and stops (releasing the hardware, turning off the menu-bar mic indicator)
/// when the last subscription is cancelled.
final class MicHub {
    static let shared = MicHub()

    private let input: MicInput
    /// Serialises starting, stopping and tap changes. Never taken on the audio
    /// thread, so the engine can wait for an in-flight tap callback without
    /// deadlocking against it.
    private let control = NSLock()
    /// Guards `handlers` only; the audio thread takes it briefly to fan out.
    private let handlersLock = NSLock()
    private var handlers: [UUID: (AVAudioPCMBuffer) -> Void] = [:]
    private var tapInstalled = false
    private var configObserver: NSObjectProtocol?

    /// The input's current format, for callers that build their own converter
    /// from the hardware rate at the moment they start (e.g. `RealtimeMicStreamer`).
    var currentFormat: AVAudioFormat { input.format }

    init(input: MicInput = SystemMicInput()) {
        self.input = input
        if let system = input as? SystemMicInput {
            // The mic's format can change under us (a headset connects, say);
            // AVAudioEngine stops itself and posts this, and we're expected to
            // reinstall the tap at the new format and restart.
            configObserver = NotificationCenter.default.addObserver(
                forName: .AVAudioEngineConfigurationChange, object: system.engine, queue: nil
            ) { [weak self] _ in
                self?.reconfigure()
            }
        }
    }

    deinit {
        if let configObserver { NotificationCenter.default.removeObserver(configObserver) }
    }

    /// Subscribe to raw mic buffers, in the input's native format. The first
    /// subscriber installs the tap and starts the microphone; if that fails, the
    /// subscription is rolled back and the error is rethrown.
    func subscribe(_ handler: @escaping (AVAudioPCMBuffer) -> Void) throws -> MicSubscription {
        control.lock()
        defer { control.unlock() }
        let id = UUID()
        handlersLock.lock()
        let isFirst = handlers.isEmpty
        handlers[id] = handler
        handlersLock.unlock()
        if isFirst {
            if !tapInstalled {
                input.installTap { [weak self] buffer, _ in
                    self?.dispatch(buffer)
                }
                tapInstalled = true
            }
            do {
                try input.start()
            } catch {
                handlersLock.lock()
                handlers.removeValue(forKey: id)
                handlersLock.unlock()
                input.removeTap()
                tapInstalled = false
                throw error
            }
        }
        return MicSubscription(id: id, hub: self)
    }

    fileprivate func unsubscribe(_ id: UUID) {
        control.lock()
        defer { control.unlock() }
        handlersLock.lock()
        handlers.removeValue(forKey: id)
        let empty = handlers.isEmpty
        handlersLock.unlock()
        guard empty else { return }
        if tapInstalled {
            input.removeTap()
            tapInstalled = false
        }
        input.stop()
    }

    private func dispatch(_ buffer: AVAudioPCMBuffer) {
        handlersLock.lock()
        let snapshot = Array(handlers.values)
        handlersLock.unlock()
        for handler in snapshot {
            handler(buffer)
        }
    }

    private func reconfigure() {
        control.lock()
        defer { control.unlock() }
        handlersLock.lock()
        let empty = handlers.isEmpty
        handlersLock.unlock()
        guard !empty else { return }
        if tapInstalled {
            input.removeTap()
            tapInstalled = false
        }
        input.installTap { [weak self] buffer, _ in
            self?.dispatch(buffer)
        }
        tapInstalled = true
        try? input.start()
    }
}

/// Converts microphone buffers of any input format to 16 kHz mono Float32 — the
/// rate every plain-float consumer (recording, meeting audio) wants. Keeps a
/// converter cached and rebuilds it only when the source format changes.
///
/// The resampler holds back a few tens of milliseconds of audio until it is told
/// the stream has ended: call `finish()` when capture stops, or the end of the
/// recording is lost. Thread-safe, because a buffer already handed out by the hub
/// can still arrive while the owner is stopping.
final class MicConverter {
    static let targetSampleRate: Double = 16_000

    private let targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                             sampleRate: MicConverter.targetSampleRate,
                                             channels: 1, interleaved: false)!
    private var converter: AVAudioConverter?
    private var sourceSampleRate: Double = 0
    private var sourceChannelCount: AVAudioChannelCount = 0
    private let lock = NSLock()

    /// Converts one buffer to 16 kHz mono `Float32` samples; empty on failure.
    func convert(_ buffer: AVAudioPCMBuffer) -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        let format = buffer.format
        if converter == nil || format.sampleRate != sourceSampleRate
            || format.channelCount != sourceChannelCount {
            converter = AVAudioConverter(from: format, to: targetFormat)
            sourceSampleRate = format.sampleRate
            sourceChannelCount = format.channelCount
        }
        guard let converter else { return [] }
        let ratio = targetFormat.sampleRate / format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity)
        else { return [] }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let channel = out.floatChannelData?[0] else { return [] }
        return Array(UnsafeBufferPointer(start: channel, count: Int(out.frameLength)))
    }

    /// The audio the resampler is still holding, at the end of a capture. Resets the
    /// converter, so the next `convert` starts a fresh stream.
    func finish() -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        guard let converter else { return [] }
        defer { converter.reset() }
        var tail: [Float] = []
        // Drain in a few passes: one output buffer may not hold everything.
        for _ in 0 ..< 8 {
            guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: 4096) else { break }
            var error: NSError?
            let status = converter.convert(to: out, error: &error) { _, inputStatus in
                inputStatus.pointee = .endOfStream
                return nil
            }
            if error == nil, let channel = out.floatChannelData?[0], out.frameLength > 0 {
                tail.append(contentsOf: UnsafeBufferPointer(start: channel, count: Int(out.frameLength)))
            }
            if error != nil || status == .endOfStream || status == .error || out.frameLength == 0 {
                break
            }
        }
        return tail
    }
}
