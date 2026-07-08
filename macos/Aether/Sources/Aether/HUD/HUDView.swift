import SwiftUI

struct HUDView: View {
    @ObservedObject var world: WorldModel
    @ObservedObject var audio: AudioEngine
    @ObservedObject var voice: VoicePipeline
    var isRunning: Bool
    var sidecarOK: Bool
    var ambientActive: Bool
    var fleetActiveCount: Int = 0
    var onStop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle()
                    .fill(statusColor)
                    .frame(width: 10, height: 10)
                Text("Aether")
                    .font(.headline)
                if fleetActiveCount > 0 {
                    Label("\(fleetActiveCount)", systemImage: "gearshape.2")
                        .font(.caption)
                        .foregroundStyle(.cyan)
                        .help("\(fleetActiveCount) agent session(s) active")
                }
                Spacer()
                if audio.isRecording {
                    Label("Listening", systemImage: "mic.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                } else if voice.isListeningDuringTTS {
                    Label("Barge-in", systemImage: "waveform")
                        .font(.caption)
                        .foregroundStyle(.orange)
                } else if isRunning {
                    ProgressView()
                        .controlSize(.small)
                } else if ambientActive {
                    Label("Ambient", systemImage: "ear")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }

            if !world.goal.isEmpty {
                Text(world.goal)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
            }

            if !world.transcript.isEmpty {
                Text(world.transcript)
                    .font(.caption)
                    .foregroundStyle(.primary.opacity(0.85))
                    .lineLimit(2)
            }

            if !voice.partialTranscript.isEmpty {
                Text(voice.partialTranscript)
                    .font(.caption2.italic())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            if !world.currentStep.isEmpty {
                Text(world.currentStep)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            HStack {
                Text(sidecarOK ? "Sidecar connected" : "Sidecar offline")
                    .font(.caption2)
                    .foregroundStyle(sidecarOK ? .green : .orange)
                Spacer()
                Button(role: .destructive, action: onStop) {
                    Label("STOP", systemImage: "stop.fill")
                        .font(.caption.weight(.bold))
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .keyboardShortcut(.cancelAction)
            }
        }
        .padding(14)
        .frame(width: 320)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.white.opacity(0.15), lineWidth: 1)
        )
    }

    private var statusColor: Color {
        switch world.status {
        case "working": return .yellow
        case "stopped": return .orange
        case "idle": return .green
        default: return .gray
        }
    }
}
