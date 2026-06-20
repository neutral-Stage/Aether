import AppKit
import AVFoundation
import SwiftUI

struct PermissionStatus {
    var accessibility: Bool
    var screenRecording: Bool?
    var microphone: Bool
    var speech: Bool

    var allRequiredGranted: Bool {
        accessibility && (microphone || speech)
    }
}

enum PermissionsChecker {
    static func status(mic: Bool, speech: Bool) -> PermissionStatus {
        PermissionStatus(
            accessibility: AccessibilityReader.isTrusted(prompt: false),
            screenRecording: screenRecordingGranted(),
            microphone: mic,
            speech: speech
        )
    }

    static func screenRecordingGranted() -> Bool? {
        if #available(macOS 10.15, *) {
            return CGPreflightScreenCaptureAccess()
        }
        return nil
    }

    static func requestAccessibility() {
        _ = AccessibilityReader.isTrusted(prompt: true)
    }

    static func requestScreenRecording() {
        if #available(macOS 10.15, *) {
            _ = CGRequestScreenCaptureAccess()
        }
    }

    static func openPrivacyPane(_ anchor: String) {
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_\(anchor)")!
        NSWorkspace.shared.open(url)
    }
}

struct OnboardingView: View {
    @ObservedObject var audio: AudioEngine
    @ObservedObject var stt: STTBridge
    @Binding var isPresented: Bool
    var onComplete: () -> Void

    @State private var status = PermissionStatus(
        accessibility: false, screenRecording: nil, microphone: false, speech: false
    )

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Welcome to Aether")
                .font(.title2.bold())
            Text("Grant permissions so Aether can see and control your Mac. The Python sidecar runs the agent loop; this app provides voice and the HUD.")
                .foregroundStyle(.secondary)

            permissionRow(
                title: "Accessibility",
                detail: "Required to read UI and synthesize input.",
                granted: status.accessibility,
                action: { PermissionsChecker.requestAccessibility() },
                settings: { PermissionsChecker.openPrivacyPane("Accessibility") }
            )
            permissionRow(
                title: "Screen Recording",
                detail: "Needed for screenshots and vision fallback.",
                granted: status.screenRecording == true,
                unknown: status.screenRecording == nil,
                action: { PermissionsChecker.requestScreenRecording() },
                settings: { PermissionsChecker.openPrivacyPane("ScreenCapture") }
            )
            permissionRow(
                title: "Microphone",
                detail: "Push-to-talk voice commands.",
                granted: status.microphone,
                action: { Task { await audio.requestMicPermission() } },
                settings: { PermissionsChecker.openPrivacyPane("Microphone") }
            )
            permissionRow(
                title: "Speech Recognition",
                detail: "On-device STT for low-latency voice.",
                granted: status.speech,
                action: { Task { await stt.requestAuthorization() } },
                settings: { PermissionsChecker.openPrivacyPane("SpeechRecognition") }
            )

            HStack {
                Spacer()
                Button("Continue") {
                    AetherConfig.hasCompletedOnboarding = true
                    isPresented = false
                    onComplete()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!status.accessibility)
            }
        }
        .padding(24)
        .frame(width: 480)
        .onAppear { refresh() }
        .onReceive(Timer.publish(every: 1.5, on: .main, in: .common).autoconnect()) { _ in
            refresh()
        }
    }

    private func refresh() {
        status = PermissionsChecker.status(mic: audio.micAuthorized, speech: stt.speechAuthorized)
    }

    @ViewBuilder
    private func permissionRow(
        title: String,
        detail: String,
        granted: Bool,
        unknown: Bool = false,
        action: @escaping () -> Void,
        settings: @escaping () -> Void
    ) -> some View {
        HStack(alignment: .top) {
            Image(systemName: granted ? "checkmark.circle.fill" : (unknown ? "questionmark.circle" : "xmark.circle"))
                .foregroundStyle(granted ? .green : .orange)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Request", action: action)
                    Button("Open Settings", action: settings)
                }
                .controlSize(.small)
            }
            Spacer()
        }
    }
}
