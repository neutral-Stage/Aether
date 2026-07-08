@preconcurrency import AVFoundation
import Foundation

@MainActor
final class AudioEngine: ObservableObject {
    @Published var isRecording = false
    @Published var micAuthorized = false
    @Published var micEnergy: Float = 0

    private let engine = AVAudioEngine()
    private var recordedFrames: [Float] = []
    private let sampleRate: Double = 16_000
    private var monitoring = false
    private var energyHandler: ((Float) -> Void)?
    private var frameHandler: (([Int16]) -> Void)?
    private var wakeConverter: AVAudioConverter?
    private var wakeFormat: AVAudioFormat?
    private var energyThreshold: Float = 0.02
    /// Mic gate: suppress energy callbacks during TTS unless barge-in threshold exceeded.
    private(set) var micGated = false
    private var gateThresholdMultiplier: Float = 2.5

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
        recordedFrames = []
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self else { return }
            guard let channel = buffer.floatChannelData?[0] else { return }
            let frames = Int(buffer.frameLength)
            self.recordedFrames.append(contentsOf: UnsafeBufferPointer(start: channel, count: frames))
            self.micEnergy = Self.rms(channel: channel, count: frames)
        }
        if !engine.isRunning { try engine.start() }
        isRecording = true
    }

    func stopRecording() -> Data {
        defer {
            if !monitoring {
                engine.inputNode.removeTap(onBus: 0)
                if !isRecording { engine.stop() }
            }
            isRecording = false
        }
        return makeWAV(from: recordedFrames, sampleRate: sampleRate)
    }

    /// Keep mic tap active for barge-in / VAD while TTS plays.
    /// Phase 7 (VOICE-001): enable hardware AEC via AVAudioEngine voice-processing
    /// I/O unit or WebRTC AEC — see docs/VOICE.md. Current path is energy-only.
    func startContinuousMonitoring(
        threshold: Float = 0.02,
        wakeSampleRate: Int = 0,
        onEnergy: @escaping (Float) -> Void,
        onFrame: (([Int16]) -> Void)? = nil
    ) throws {
        guard !monitoring else { return }
        monitoring = true
        energyThreshold = threshold
        energyHandler = onEnergy
        frameHandler = onFrame
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        // Set up a converter to the wake engine's rate (e.g. Porcupine 16 kHz mono Int16).
        var target: AVAudioFormat?
        var converter: AVAudioConverter?
        if onFrame != nil, wakeSampleRate > 0,
           let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                   sampleRate: Double(wakeSampleRate),
                                   channels: 1, interleaved: true) {
            target = fmt
            converter = AVAudioConverter(from: format, to: fmt)
        }
        wakeFormat = target
        wakeConverter = converter
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self else { return }
            guard let channel = buffer.floatChannelData?[0] else { return }
            let frames = Int(buffer.frameLength)
            let energy = Self.rms(channel: channel, count: frames)
            let pcm = Self.convertForWake(buffer, converter: converter, target: target)
            Task { @MainActor in
                self.micEnergy = energy
                if let pcm, !pcm.isEmpty { self.frameHandler?(pcm) }
                if self.micGated && energy < self.energyThreshold * self.gateThresholdMultiplier {
                    return
                }
                if energy > self.energyThreshold {
                    self.energyHandler?(energy)
                }
            }
        }
        if !engine.isRunning { try engine.start() }
    }

    func stopContinuousMonitoring() {
        guard monitoring else { return }
        monitoring = false
        energyHandler = nil
        frameHandler = nil
        wakeConverter = nil
        wakeFormat = nil
        engine.inputNode.removeTap(onBus: 0)
        if !isRecording {
            engine.stop()
        }
        micEnergy = 0
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

    private static func rms(channel: UnsafePointer<Float>, count: Int) -> Float {
        guard count > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<count {
            let s = channel[i]
            sum += s * s
        }
        return sqrtf(sum / Float(count))
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
