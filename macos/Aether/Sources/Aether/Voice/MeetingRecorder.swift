import AppKit
import AVFoundation
import CoreMedia
import ScreenCaptureKit
import SwiftUI

/// Pure helpers for meeting audio: app choice, resampling, silence, WAV encoding.
enum MeetingAudio {
    static let sampleRate = 16_000
    /// Dedicated meeting apps, preferred over whatever is in front.
    static let meetingApps = ["us.zoom.xos", "com.microsoft.teams2", "com.microsoft.teams",
                              "com.apple.FaceTime", "com.cisco.webexmeetingsapp",
                              "Cisco-Systems.Spark", "com.tinyspeck.slackmacgap", "com.hnc.Discord"]

    /// The app whose audio is "them": the front app if it is a meeting app, else a running
    /// meeting app, else the front app (a browser with Meet, say). Never Aether.
    static func pickApp(running: [String], frontmost: String?, own: String?) -> String? {
        if let front = frontmost, meetingApps.contains(front) { return front }
        if let app = meetingApps.first(where: { running.contains($0) }) { return app }
        if let front = frontmost, front != own { return front }
        return nil
    }

    static func resample(_ s: [Float], from: Double, to: Double) -> [Float] {
        guard from > 0, to > 0, !s.isEmpty else { return [] }
        if abs(from - to) < 0.5 { return s }
        let ratio = from / to
        let n = Int(Double(s.count) / ratio)
        guard n > 0 else { return [] }
        var out = [Float](repeating: 0, count: n)
        for i in 0 ..< n {
            let pos = Double(i) * ratio
            let j = Int(pos)
            let frac = Float(pos - Double(j))
            let a = s[min(j, s.count - 1)]
            let b = s[min(j + 1, s.count - 1)]
            out[i] = a + (b - a) * frac
        }
        return out
    }

    static func rms(_ s: [Float]) -> Float {
        guard !s.isEmpty else { return 0 }
        var sum: Float = 0
        for v in s { sum += v * v }
        return (sum / Float(s.count)).squareRoot()
    }

    /// Whisper invents words in silence, so quiet chunks are not sent.
    static func worthSending(_ s: [Float]) -> Bool {
        s.count >= sampleRate / 2 && rms(s) >= 0.004
    }

    /// 16-bit PCM mono WAV.
    static func wav(_ samples: [Float], sampleRate: Int = MeetingAudio.sampleRate) -> Data {
        var pcm = Data(capacity: samples.count * 2)
        for s in samples {
            let v = Int16(max(-1, min(1, s)) * Float(Int16.max))
            withUnsafeBytes(of: v.littleEndian) { pcm.append(contentsOf: $0) }
        }
        var d = Data()
        func u32(_ x: UInt32) { withUnsafeBytes(of: x.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ x: UInt16) { withUnsafeBytes(of: x.littleEndian) { d.append(contentsOf: $0) } }
        d.append(contentsOf: Array("RIFF".utf8))
        u32(UInt32(36 + pcm.count))
        d.append(contentsOf: Array("WAVEfmt ".utf8))
        u32(16)
        u16(1)                              // PCM
        u16(1)                              // mono
        u32(UInt32(sampleRate))
        u32(UInt32(sampleRate * 2))         // bytes per second
        u16(2)                              // block align
        u16(16)                             // bits per sample
        d.append(contentsOf: Array("data".utf8))
        u32(UInt32(pcm.count))
        d.append(pcm)
        return d
    }

    /// Mono Float32 samples and their rate from a ScreenCaptureKit audio buffer.
    static func monoFloats(from sampleBuffer: CMSampleBuffer) -> (samples: [Float], rate: Double) {
        guard let desc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)?.pointee,
              asbd.mFormatID == kAudioFormatLinearPCM,
              asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              asbd.mBitsPerChannel == 32 else { return ([], 0) }
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let interleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved == 0
        var out: [Float] = []
        try? sampleBuffer.withAudioBufferList { list, _ in
            guard let first = list.first, let data = first.mData else { return }
            let count = Int(first.mDataByteSize) / MemoryLayout<Float>.size
            let floats = UnsafeBufferPointer(start: data.assumingMemoryBound(to: Float.self),
                                             count: count)
            if interleaved && channels > 1 {
                out.reserveCapacity(count / channels)
                var i = 0
                while i + channels <= count {
                    var sum: Float = 0
                    for c in 0 ..< channels { sum += floats[i + c] }
                    out.append(sum / Float(channels))
                    i += channels
                }
            } else {
                out = Array(floats)          // first (or only) channel
            }
        }
        return (out, asbd.mSampleRate)
    }
}

/// Samples waiting to be sent, shared between an audio thread and the main actor.
final class MeetingSampleBuffer {
    private var samples: [Float] = []
    private let lock = NSLock()
    /// Two minutes at most, if uploads stall.
    private let cap = MeetingAudio.sampleRate * 120

    func append(_ s: [Float]) {
        lock.lock()
        samples.append(contentsOf: s)
        if samples.count > cap { samples.removeFirst(samples.count - cap) }
        lock.unlock()
    }

    func take() -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        let s = samples
        samples = []
        return s
    }
}

enum MeetingCaptureError: LocalizedError {
    case noDisplay, appNotFound, noMicrophone

    var errorDescription: String? {
        switch self {
        case .noDisplay: return "no display to capture from"
        case .appNotFound: return "the meeting app isn't available to capture"
        case .noMicrophone: return "no microphone input"
        }
    }
}

/// The meeting app's audio only (ScreenCaptureKit), without Aether's own sounds.
final class AppAudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    let buffer = MeetingSampleBuffer()
    var onStopped: ((Error) -> Void)?
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "aether.meeting.app-audio")

    func start(bundleId: String) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                           onScreenWindowsOnly: false)
        guard let display = content.displays.first else { throw MeetingCaptureError.noDisplay }
        let apps = content.applications.filter { $0.bundleIdentifier == bundleId }
        guard !apps.isEmpty else { throw MeetingCaptureError.appNotFound }
        let filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = MeetingAudio.sampleRate
        config.channelCount = 1
        config.width = 2                    // video is required but unused
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await s.startCapture()
        stream = s
    }

    func stop() async {
        guard let s = stream else { return }
        stream = nil
        try? await s.stopCapture()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        let (samples, rate) = MeetingAudio.monoFloats(from: sampleBuffer)
        guard !samples.isEmpty else { return }
        buffer.append(MeetingAudio.resample(samples, from: rate, to: Double(MeetingAudio.sampleRate)))
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        self.stream = nil
        onStopped?(error)
    }
}

/// The user's microphone ("me"), on its own engine so push-to-talk keeps working.
final class MicTap {
    let buffer = MeetingSampleBuffer()
    private let engine = AVAudioEngine()
    private var running = false

    func start() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw MeetingCaptureError.noMicrophone
        }
        let rate = format.sampleRate
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] pcm, _ in
            guard let self, let channels = pcm.floatChannelData else { return }
            let mono = Array(UnsafeBufferPointer(start: channels[0], count: Int(pcm.frameLength)))
            self.buffer.append(MeetingAudio.resample(mono, from: rate,
                                                     to: Double(MeetingAudio.sampleRate)))
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    func stop() {
        guard running else { return }
        running = false
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}

/// What the sidecar will do with meeting audio (GET /meetings/transcription).
struct MeetingTranscription: Equatable {
    let engine: String
    let ready: Bool
    let message: String
    let summarize: Bool

    static func parse(_ o: [String: Any]) -> MeetingTranscription {
        MeetingTranscription(engine: o["engine"] as? String ?? "", ready: o["ready"] as? Bool ?? false,
                             message: o["message"] as? String ?? "",
                             summarize: o["summarize"] as? Bool ?? false)
    }

    /// Where the audio goes, for the consent prompt.
    var whereText: String {
        engine == "local" ? "on this Mac" : "with \(engine.capitalized)"
    }
}

struct ActiveMeeting: Equatable {
    let id: String
    let title: String
    let app: String
    let started: Date
}

/// Meeting notes: consent, capture, ~30 s uploads, and the notes when it stops.
@MainActor
final class MeetingRecorder: ObservableObject {
    @Published private(set) var active: ActiveMeeting?
    var onStatus: ((String) -> Void)?

    private let client: OrchestratorClient
    private var appTap: AppAudioTap?
    private var micTap: MicTap?
    private var bundleId = ""
    private var loop: Task<Void, Never>?
    private var sent: [String: Int] = [:]          // samples consumed per channel
    private var uploadFailed = false
    private var restarts = 0
    private var wakeObserver: Any?
    private let resultPanel = SkillResultPanel()

    init(client: OrchestratorClient) {
        self.client = client
    }

    func start() async {
        guard active == nil else { return }
        guard let status = await client.meetingTranscription() else {
            onStatus?("Meeting notes need the sidecar; it isn't reachable")
            return
        }
        guard status.ready else {
            onStatus?(status.message)
            return
        }
        let running = NSWorkspace.shared.runningApplications.compactMap(\.bundleIdentifier)
        let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        guard let bundle = MeetingAudio.pickApp(running: running, frontmost: front,
                                                own: Bundle.main.bundleIdentifier) else {
            onStatus?("Open the meeting app first")
            return
        }
        let appName = NSWorkspace.shared.runningApplications
            .first { $0.bundleIdentifier == bundle }?.localizedName ?? bundle
        guard consent(appName: appName, status: status) else { return }
        do {
            let (id, title) = try await client.startMeeting(app: appName)
            bundleId = bundle
            sent = [:]
            uploadFailed = false
            restarts = 0
            try await beginCapture(appName: appName)
            active = ActiveMeeting(id: id, title: title, app: appName, started: Date())
            onStatus?("Taking notes of \(appName) · stop from the menu bar")
            loop = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 30_000_000_000)
                    guard let self, !Task.isCancelled else { return }
                    await self.flush()
                }
            }
            wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in await self?.restartAppTap() }
            }
        } catch {
            await stopCapture()
            onStatus?("Couldn't start meeting notes: \(error.localizedDescription)")
        }
    }

    func stop() async {
        guard let meeting = active else { return }
        loop?.cancel()
        loop = nil
        if let wakeObserver { NSWorkspace.shared.notificationCenter.removeObserver(wakeObserver) }
        wakeObserver = nil
        await stopCapture()
        await flush()
        active = nil
        onStatus?("Writing meeting notes…")
        do {
            let text = try await client.stopMeeting(id: meeting.id)
            resultPanel.show(title: "Meeting notes · \(meeting.title)", text: text)
            onStatus?("Meeting notes ready")
        } catch {
            onStatus?("The transcript is saved, but the notes failed: \(error.localizedDescription)")
        }
    }

    private func consent(appName: String, status: MeetingTranscription) -> Bool {
        let alert = NSAlert()
        alert.messageText = "Take notes of this meeting?"
        var info = "Aether will transcribe \(appName)'s audio and your microphone "
            + "\(status.whereText). The audio is not kept; the transcript is saved on this Mac."
        if status.summarize {
            info += " When you stop, your AI model writes a summary and action items from it."
        }
        info += "\n\nLet the other people in the meeting know you're taking notes."
        alert.informativeText = info
        alert.addButton(withTitle: "Start")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func beginCapture(appName: String) async throws {
        let mic = MicTap()
        var micError: Error?
        do { try mic.start() } catch { micError = error }
        if micError == nil { micTap = mic }
        let tap = makeAppTap()
        do {
            try await tap.start(bundleId: bundleId)
            appTap = tap
        } catch {
            if micTap == nil { throw micError ?? error }
            onStatus?("Couldn't capture \(appName)'s audio (\(error.localizedDescription)); "
                      + "recording your microphone only")
        }
    }

    private func makeAppTap() -> AppAudioTap {
        let tap = AppAudioTap()
        tap.onStopped = { [weak self] _ in
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                await self?.restartAppTap()
            }
        }
        return tap
    }

    /// After sleep, or when capture stopped on its own (at most 5 times per meeting).
    private func restartAppTap() async {
        guard active != nil, restarts < 5 else { return }
        restarts += 1
        let tap = appTap ?? makeAppTap()
        await tap.stop()
        do {
            try await tap.start(bundleId: bundleId)
            appTap = tap
        } catch {
            onStatus?("Lost the meeting app's audio: \(error.localizedDescription)")
        }
    }

    private func stopCapture() async {
        await appTap?.stop()
        micTap?.stop()
    }

    private func flush() async {
        guard let id = active?.id else { return }
        for (channel, buffer) in [("them", appTap?.buffer), ("me", micTap?.buffer)] {
            guard let samples = buffer?.take(), !samples.isEmpty else { continue }
            let offset = Double(sent[channel, default: 0]) / Double(MeetingAudio.sampleRate)
            sent[channel, default: 0] += samples.count
            guard MeetingAudio.worthSending(samples) else { continue }
            do {
                try await client.uploadMeetingAudio(id: id, channel: channel, offset: offset,
                                                    wav: MeetingAudio.wav(samples))
            } catch {
                if !uploadFailed {
                    uploadFailed = true
                    onStatus?("A piece of the meeting couldn't be transcribed: "
                              + error.localizedDescription)
                }
            }
        }
    }
}

/// The menu bar section for meeting notes.
struct MeetingMenu: View {
    @ObservedObject var recorder: MeetingRecorder

    var body: some View {
        if let meeting = recorder.active {
            Text("Meeting notes").font(.caption2).foregroundStyle(.secondary)
            HStack {
                Image(systemName: "record.circle").foregroundStyle(.red)
                Text("\(meeting.app) · ").font(.caption)
                    + Text(meeting.started, style: .timer).font(.caption)
            }
            Button("Stop and write notes") { Task { await recorder.stop() } }
        } else {
            Button("Take meeting notes…") { Task { await recorder.start() } }
        }
    }
}
