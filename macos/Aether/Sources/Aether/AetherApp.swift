import AppKit
import SwiftUI

@main
struct AetherApp: App {
    @StateObject private var app = AppState()

    var body: some Scene {
        MenuBarExtra("Aether", systemImage: "sparkles") {
            VStack(alignment: .leading, spacing: 8) {
                if app.client.isRunning {
                    Text(app.world.currentStep.isEmpty ? "Working…" : app.world.currentStep)
                        .font(.caption)
                        .lineLimit(2)
                }
                Button("Open Window") { app.showMainWindow = true }
                Button("Command Bar (⌥Space)") { app.toggleCommandBar() }
                Button("Refresh sidecar") { Task { await app.client.checkHealth(); app.refreshHUD() } }
                Divider()
                Button("STOP", role: .destructive) { app.handleStop() }
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
            .padding(8)
        }
        .menuBarExtraStyle(.window)

        Window("Aether", id: "main") {
            MainWindowView(app: app)
                .sheet(isPresented: $app.showOnboarding) {
                    OnboardingView(
                        audio: app.audio,
                        stt: app.stt,
                        isPresented: $app.showOnboarding,
                        onComplete: { app.refreshHUD() }
                    )
                }
                .onAppear {
                    app.bootstrap()
                    app.refreshHUD()
                    if !AetherConfig.hasCompletedOnboarding {
                        app.showOnboarding = true
                    }
                }
        }
        .defaultSize(width: 440, height: 300)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}