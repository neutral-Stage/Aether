@preconcurrency import AVFoundation
import Foundation

/// Samples from the recording tap, appended on the audio thread and read back
/// on the main actor when recording stops. Mirrors `MeetingSampleBuffer`.
private final class RecordingAccumulator {
    private var samples: [Float] = []
    private let lock = NSLock()

    func append(_ s: [Float]) {
        lock.lock()
        samples.append(contentsOf: s)
        lock.unlock()
    }

    func takeAll() -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        let s = samples
        samples = []
        return s
    }
}

@MainActor
final class AudioEngine: ObservableObject {
    @Published var isRecording = false
    @Published var micAuthorized = false
    @Published var micEnergy: Float = 0

    private let hub: MicHub
    private let sampleRate: Double = 16_000
    private var recordingSubscription: MicSubscription?
    private var recordingBuffer: RecordingAccumulator?
    /// One continuous-monitoring subscription per owner (e.g. "barge-in", "ambient"),
    /// so several features can keep the mic open for VAD/wake at the same time.
    private var monitors: [String: MicSubscription] = [:]
    /// Mic gate: suppress energy callbacks during TTS unless barge-in threshold exceeded.
    private(set) var micGated = false
    private var gateThresholdMultiplier: Float = 2.5

    init(hub: MicHub = .shared) {
        self.hub = hub
    }

    /// Practical AEC substitute (Phase 7): duck playback + gate mic during TTS.
    /// True hardware AEC requires AVAudioEngine voice-processing I/O or WebRTC —
    /// see `docs/VOICE.md` limitations section.
    func setMicGated(_ gated: Bool) {
        micGated = gated
    }

    func configureGate(multiplier: Float = 2.5) {
        gateThresholdMultiplier = multiplier
    }

    func refreshMicPermission() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            micAuthorized = true
        default:
            micAuthorized = false
        }
    }

    func requestMicPermission() async -> Bool {
        let ok = await AVCaptureDevice.requestAccess(for: .audio)
        await MainActor.run { micAuthorized = ok }
        return ok
    }

    func startRecording() throws {
        guard !isRecording else { return }
        let buffer = RecordingAccumulator()
        let converter = MicConverter()
        recordingBuffer = buffer
        recordingSubscription = try hub.subscribe { [weak self] pcm in
            let samples = converter.convert(pcm)
            guard !samples.isEmpty else { return }
            buffer.append(samples)
            let energy = AudioEngine.rms(samples)
            Task { @MainActor in
                self?.micEnergy = energy
            }
        }
        isRecording = true
    }

    /// Cancels the recording subscription (releasing the mic if nothing else is
    /// listening) and returns what was recorded as a genuinely 16 kHz mono WAV.
    func stopRecording() -> Data {
        recordingSubscription?.cancel()
        recordingSubscription = nil
        isRecording = false
        let samples = recordingBuffer?.takeAll() ?? []
        recordingBuffer = nil
        return makeWAV(from: samples, sampleRate: sampleRate)
    }

    /// Keep the mic open for barge-in / VAD / wake word while other things happen.
    /// Several owners can monitor at once, each with its own subscription — e.g.
    /// barge-in and ambient wake no longer fight over a single monitoring flag.
    /// Phase 7 (VOICE-001): enable hardware AEC via AVAudioEngine voice-processing
    /// I/O unit or WebRTC AEC — see docs/VOICE.md. Current path is energy-only.
    func startContinuousMonitoring(
        owner: String,
        threshold: Float = 0.02,
        wakeSampleRate: Int = 0,
        onEnergy: @escaping (Float) -> Void,
        onFrame: (([Int16]) -> Void)? = nil
    ) throws {
        stopContinuousMonitoring(owner: owner)

        // Set up a converter to the wake engine's rate (e.g. Porcupine 16 kHz mono Int16).
        var target: AVAudioFormat?
        var converter: AVAudioConverter?
        if onFrame != nil, wakeSampleRate > 0,
           let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                   sampleRate: Double(wakeSampleRate),
                                   channels: 1, interleaved: true) {
            target = fmt
            converter = AVAudioConverter(from: hub.currentFormat, to: fmt)
        }

        let subscription = try hub.subscribe { [weak self] buffer in
            guard let channel = buffer.floatChannelData?[0] else { return }
            let frames = Int(buffer.frameLength)
            let energy = AudioEngine.rms(channel: channel, count: frames)
            let pcm = AudioEngine.convertForWake(buffer, converter: converter, target: target)
            Task { @MainActor in
                guard let self else { return }
                self.micEnergy = energy
                if let pcm, !pcm.isEmpty { onFrame?(pcm) }
                if self.micGated && energy < threshold * self.gateThresholdMultiplier {
                    return
                }
                if energy > threshold {
                    onEnergy(energy)
                }
            }
        }
        monitors[owner] = subscription
    }

    func stopContinuousMonitoring(owner: String) {
        guard let subscription = monitors.removeValue(forKey: owner) else { return }
        subscription.cancel()
        if monitors.isEmpty {
            micEnergy = 0
        }
    }

    /// Single-owner convenience for callers that only ever run one monitor.
    func startContinuousMonitoring(
        threshold: Float = 0.02,
        wakeSampleRate: Int = 0,
        onEnergy: @escaping (Float) -> Void,
        onFrame: (([Int16]) -> Void)? = nil
    ) throws {
        try startContinuousMonitoring(owner: "default", threshold: threshold,
                                      wakeSampleRate: wakeSampleRate, onEnergy: onEnergy,
                                      onFrame: onFrame)
    }

    func stopContinuousMonitoring() {
        stopContinuousMonitoring(owner: "default")
    }

    /// Resample a mic buffer to the wake engine's Int16 PCM (nil if no converter).
    private nonisolated static func convertForWake(
        _ buffer: AVAudioPCMBuffer,
        converter: AVAudioConverter?,
        target: AVAudioFormat?
    ) -> [Int16]? {
        guard let converter, let target else { return nil }
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity)
        else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let ch = out.int16ChannelData else { return nil }
        return Array(UnsafeBufferPointer(start: ch[0], count: Int(out.frameLength)))
    }

    private nonisolated static func rms(channel: UnsafePointer<Float>, count: Int) -> Float {
        guard count > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<count {
            let s = channel[i]
            sum += s * s
        }
        return sqrtf(sum / Float(count))
    }

    private nonisolated static func rms(_ samples: [Float]) -> Float {
        guard !samples.isEmpty else { return 0 }
        var sum: Float = 0
        for s in samples { sum += s * s }
        return sqrtf(sum / Float(samples.count))
    }

    private func makeWAV(from samples: [Float], sampleRate: Double) -> Data {
        var pcm = Data()
        for s in samples {
            let clamped = max(-1, min(1, s))
            var i16 = Int16(clamped * Float(Int16.max))
            withUnsafeBytes(of: &i16) { pcm.append(contentsOf: $0) }
        }
        let byteRate = UInt32(sampleRate) * 2
        var header = Data()
        header.append(contentsOf: "RIFF".utf8)
        var chunkSize = UInt32(36 + pcm.count).littleEndian
        header.append(Data(bytes: &chunkSize, count: 4))
        header.append(contentsOf: "WAVE".utf8)
        header.append(contentsOf: "fmt ".utf8)
        var subchunk1 = UInt32(16).littleEndian
        header.append(Data(bytes: &subchunk1, count: 4))
        var audioFormat = UInt16(1).littleEndian
        header.append(Data(bytes: &audioFormat, count: 2))
        var channels = UInt16(1).littleEndian
        header.append(Data(bytes: &channels, count: 2))
        var sr = UInt32(sampleRate).littleEndian
        header.append(Data(bytes: &sr, count: 4))
        var br = byteRate.littleEndian
        header.append(Data(bytes: &br, count: 4))
        var blockAlign = UInt16(2).littleEndian
        header.append(Data(bytes: &blockAlign, count: 2))
        var bps = UInt16(16).littleEndian
        header.append(Data(bytes: &bps, count: 2))
        header.append(contentsOf: "data".utf8)
        var dataSize = UInt32(pcm.count).littleEndian
        header.append(Data(bytes: &dataSize, count: 4))
        header.append(pcm)
        return header
    }
}
