import AppKit
import AVFoundation
import SwiftUI

struct PermissionStatus {
    var accessibility: Bool
    var screenRecording: Bool?
    var microphone: Bool
    var speech: Bool
    /// Global hotkeys (push-to-talk, STOP, command bar) need Input Monitoring.
    var inputMonitoring: Bool = false

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
            speech: speech,
            inputMonitoring: CGPreflightListenEventAccess()
        )
    }

    static func requestInputMonitoring() {
        _ = CGRequestListenEventAccess()
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
    var client: OrchestratorClient? = nil
    var onKeysSaved: () -> Void = {}
    var onComplete: () -> Void

    @State private var status = PermissionStatus(
        accessibility: false, screenRecording: nil, microphone: false, speech: false
    )
    @State private var zaiKey = ""
    @State private var groqKey = ""
    @State private var anthropicKey = ""
    @State private var keysSaved = false
    @State private var doctor: DoctorReport?
    @State private var checking = false

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 16) {
            Text("Welcome to Aether")
                .font(.title2.bold())
            Text("Grant permissions, add an API key, check the setup, and tell Aether a little about your work. Aether's agent runs in a local sidecar; this app provides the HUD, voice and hotkeys.")
                .foregroundStyle(.secondary)

            Text("1 · Permissions").font(.headline)

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
            permissionRow(
                title: "Input Monitoring",
                detail: "Global hotkeys: hold ⌃Space to talk, ⌥Space command bar, ⌃⇧S STOP.",
                granted: status.inputMonitoring,
                action: { PermissionsChecker.requestInputMonitoring() },
                settings: { PermissionsChecker.openPrivacyPane("ListenEvent") }
            )
            Text("Screen Recording and Input Monitoring take effect after Aether restarts.")
                .font(.caption2)
                .foregroundStyle(.secondary)

            Divider()
            Text("2 · API key").font(.headline)
            keyField("Z.ai (default brain: GLM-5.3-Flash)", account: "ZAI_API_KEY", text: $zaiKey)
            keyField("Groq (voice, optional)", account: "GROQ_API_KEY", text: $groqKey)
            keyField("Anthropic (failover, optional)", account: "ANTHROPIC_API_KEY", text: $anthropicKey)
            HStack {
                Button("Save keys") { saveKeys() }
                    .disabled([zaiKey, groqKey, anthropicKey].allSatisfy {
                        $0.trimmingCharacters(in: .whitespaces).isEmpty
                    })
                if keysSaved {
                    Text("Saved to Keychain — sidecar restarting").font(.caption2).foregroundStyle(.green)
                }
            }

            Divider()
            Text("3 · Check setup").font(.headline)
            HStack {
                Button(checking ? "Checking…" : "Run checks") { runDoctor(online: false) }
                    .disabled(checking || client == nil)
                Button("Test API key") { runDoctor(online: true) }
                    .disabled(checking || client == nil)
            }
            if let report = doctor {
                ForEach(report.checks) { check in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: icon(for: check.status))
                            .foregroundStyle(color(for: check.status))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(check.name).font(.caption.weight(.semibold))
                            if !check.detail.isEmpty {
                                Text(check.detail).font(.caption2).foregroundStyle(.secondary)
                            }
                            if check.status != "ok", !check.fix.isEmpty {
                                Text(check.fix).font(.caption2).foregroundStyle(.orange)
                            }
                        }
                    }
                }
            }

            if let client {
                Divider()
                Text("4 · About you (optional)").font(.headline)
                AboutYouView(client: client)
            }

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
        }
        .frame(width: 520, height: 640)
        .onAppear { refresh() }
        .onReceive(Timer.publish(every: 1.5, on: .main, in: .common).autoconnect()) { _ in
            refresh()
        }
    }

    private func refresh() {
        status = PermissionsChecker.status(mic: audio.micAuthorized, speech: stt.speechAuthorized)
    }

    private func saveKeys() {
        for (account, value) in [("ZAI_API_KEY", zaiKey), ("GROQ_API_KEY", groqKey),
                                 ("ANTHROPIC_API_KEY", anthropicKey)] {
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                KeyStore.setProviderKey(account, trimmed)
            }
        }
        zaiKey = ""
        groqKey = ""
        anthropicKey = ""
        keysSaved = true
        onKeysSaved()
    }

    private func runDoctor(online: Bool) {
        guard let client else { return }
        checking = true
        Task {
            // Give a freshly restarted sidecar a moment to come up.
            for _ in 0..<10 {
                if let report = await client.fetchDoctor(online: online) {
                    doctor = report
                    break
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            checking = false
        }
    }

    private func icon(for status: String) -> String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "warn": return "exclamationmark.triangle.fill"
        default: return "xmark.circle.fill"
        }
    }

    private func color(for status: String) -> Color {
        switch status {
        case "ok": return .green
        case "warn": return .orange
        default: return .red
        }
    }

    @ViewBuilder
    private func keyField(_ title: String, account: String, text: Binding<String>) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .font(.caption)
                .frame(width: 230, alignment: .leading)
            SecureField(KeyStore.hasProviderKey(account) ? "•••• saved" : "paste key", text: text)
                .textFieldStyle(.roundedBorder)
                .font(.caption)
            if KeyStore.hasProviderKey(account) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            }
        }
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
