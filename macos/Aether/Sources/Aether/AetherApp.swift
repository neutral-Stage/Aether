import AppKit
import SwiftUI

@main
struct AetherApp: App {
    @StateObject private var app = AppState()

    var body: some Scene {
        MenuBarExtra {
            VStack(alignment: .leading, spacing: 8) {
                if app.client.isRunning {
                    Text(app.world.currentStep.isEmpty ? "Working…" : app.world.currentStep)
                        .font(.caption)
                        .lineLimit(2)
                }
                Button("Chat (⌃⌘A)") { app.openChat() }
                RecentChatsMenu(chat: app.chat) { app.openChat(session: $0) }
                QuickSkillsMenu(controller: app.quickSkills)
                ScreenMemoryMenu(controller: app.screenMemory)
                MeetingMenu(recorder: app.meetings)
                Button("Open Window") { app.showMainWindow = true }
                Button("Command Bar (⌥Space)") { app.toggleCommandBar() }
                Button("New Conversation") { app.newConversation() }
                Button("Refresh sidecar") { Task { await app.client.checkHealth(); app.refreshHUD() } }
                Divider()
                Button("STOP", role: .destructive) { app.handleStop() }
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
            .padding(8)
            .task {
                await app.chat.refreshSessions()
                await app.screenMemory.refresh()
            }
        } label: {
            MenuBarIcon(screenMemory: app.screenMemory, meetings: app.meetings)
        }
        .menuBarExtraStyle(.window)

        Window("Aether", id: "main") {
            MainWindowView(app: app)
                .sheet(isPresented: $app.showOnboarding) {
                    OnboardingView(
                        audio: app.audio,
                        stt: app.stt,
                        isPresented: $app.showOnboarding,
                        client: app.client,
                        onKeysSaved: { app.sidecar.restart() },
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

/// The last few conversations, one click to reopen.
struct RecentChatsMenu: View {
    @ObservedObject var chat: ChatStore
    var open: (String) -> Void

    var body: some View {
        if !chat.sessions.isEmpty {
            Text("Recent").font(.caption2).foregroundStyle(.secondary)
            ForEach(chat.sessions.prefix(5)) { s in
                Button("  " + s.title) { open(s.id) }
                    .lineLimit(1)
            }
        }
    }
}

/// Quick skills, one click to run (the hotkey is shown when there is one).
struct QuickSkillsMenu: View {
    @ObservedObject var controller: QuickSkillsController

    var body: some View {
        if !controller.skills.isEmpty {
            Text("Quick skills").font(.caption2).foregroundStyle(.secondary)
            ForEach(controller.skills) { skill in
                Button("  " + skill.name + (skill.hotkey.isEmpty ? "" : "  ⌃⌥\(skill.hotkey)")) {
                    Task { await controller.trigger(skill.id) }
                }
                .lineLimit(1)
            }
        }
    }
}
