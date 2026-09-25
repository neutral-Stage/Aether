import AppKit
import Foundation
import SwiftUI

/// State behind the chat window: the conversation list and the open conversation.
@MainActor
final class ChatStore: ObservableObject {
    @Published var sessions: [ChatSessionSummary] = []
    /// The open conversation (nil: a new one, created by the first message).
    @Published var selected: String?
    @Published var transcript = ChatTranscript()
    @Published var draft = ""
    @Published var loadError: String?
    /// What the user allowed for the rest of the open conversation.
    @Published var grants: [String] = []
    /// What tasks usually cost and the per-task limit, shown under the text field.
    @Published var estimate = ""

    weak var app: AppState?
    private let client: OrchestratorClient

    init(client: OrchestratorClient) {
        self.client = client
    }

    func refreshSessions() async {
        if let rows = try? await client.listSessions() {
            sessions = rows
        }
        estimate = await client.fetchEstimate() ?? ""
    }

    func open(_ id: String?) async {
        guard !transcript.isWorking else { return }
        selected = id
        transcript = ChatTranscript()
        loadError = nil
        grants = []
        guard let id else { return }
        do {
            transcript.load(turns: try await client.fetchSession(id))
        } catch {
            loadError = error.localizedDescription
        }
        await refreshGrants()
    }

    func refreshGrants() async {
        guard let id = selected else {
            grants = []
            return
        }
        grants = await client.listGrants(session: id)
    }

    func revokeGrants() {
        guard let id = selected else { return }
        Task {
            await client.revokeGrants(session: id)
            await refreshGrants()
        }
    }

    func newChat() {
        Task { await open(nil) }
    }

    func delete(_ id: String) {
        Task {
            await client.deleteSession(id)
            if selected == id { await open(nil) }
            await refreshSessions()
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !transcript.isWorking else { return }
        draft = ""
        submit(text)
    }

    func retry(_ message: ChatMessage) {
        guard !message.goal.isEmpty, !transcript.isWorking else { return }
        submit(message.goal)
    }

    func stop() {
        app?.handleStop()
    }

    private func submit(_ text: String) {
        guard let app else { return }
        if GuideIntent.isGuideRequest(text) {
            transcript.note("Showing you how on screen, one step at a time…")
            app.submitGoal(text)
            return
        }
        transcript.send(text)
        app.submitGoal(text, chatSession: selected) { [weak self] event in
            guard let self else { return }
            if case .session(let sid) = event, self.selected == nil {
                self.selected = sid
            }
            self.transcript.apply(event)
            switch event {
            case .done, .error, .stopped:
                Task {
                    await self.refreshSessions()
                    await self.refreshGrants()
                }
            default:
                break
            }
        }
    }
}

/// The chat window (⌃⌘A), an ordinary AppKit window so hotkeys and the menu can open it.
@MainActor
final class ChatWindowController {
    private var window: NSWindow?

    func show(store: ChatStore) {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 780, height: 560),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = "Aether"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: ChatView(store: store))
            w.setFrameAutosaveName("AetherChat")
            if w.frame.origin == .zero { w.center() }
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        Task { await store.refreshSessions() }
    }
}
