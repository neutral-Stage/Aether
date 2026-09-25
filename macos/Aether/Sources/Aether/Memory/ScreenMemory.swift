import AppKit
import SwiftUI

/// What the sidecar says about screen memory (GET /screen-memory/status).
struct ScreenMemoryStatus: Equatable {
    var enabled = false
    var paused = false
    var running = false
    var captures = 0
    var lastApp = ""
    var lastTime = ""

    /// True only while text is actually being remembered: drives the menu bar indicator.
    var isRecording: Bool { enabled && running && !paused }

    var summary: String {
        guard enabled else { return "Screen memory is off" }
        let saved = "\(captures) saved"
        if paused { return "Paused · \(saved)" }
        if !running { return "Not running · \(saved)" }
        let last = lastApp.isEmpty ? "" : " · last: \(lastApp) \(lastTime)"
        return "Remembering screen text · \(saved)\(last)"
    }

    static func parse(_ obj: [String: Any]) -> ScreenMemoryStatus {
        let last = obj["last"] as? [String: Any]
        let time = (last?["time"] as? String ?? "").split(separator: " ").last.map(String.init) ?? ""
        return ScreenMemoryStatus(enabled: obj["enabled"] as? Bool ?? false,
                                  paused: obj["paused"] as? Bool ?? false,
                                  running: obj["running"] as? Bool ?? false,
                                  captures: obj["captures"] as? Int ?? 0,
                                  lastApp: last?["app"] as? String ?? "",
                                  lastTime: time)
    }
}

/// Status, pause and resume, and delete for screen memory, from the menu bar.
@MainActor
final class ScreenMemoryController: ObservableObject {
    @Published private(set) var status = ScreenMemoryStatus()
    var onStatus: ((String) -> Void)?

    private let client: OrchestratorClient
    private var pollTask: Task<Void, Never>?

    init(client: OrchestratorClient) {
        self.client = client
    }

    /// Keeps the indicator honest (a sidecar restart can change the state).
    func start() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
            }
        }
    }

    func refresh() async {
        // Unreachable sidecar: nothing is recording, so show nothing.
        status = await client.screenMemoryStatus() ?? ScreenMemoryStatus()
    }

    func setPaused(_ paused: Bool) async {
        do {
            try await client.setScreenMemoryPaused(paused)
            onStatus?(paused ? "Screen memory paused" : "Screen memory resumed")
        } catch {
            onStatus?("Couldn't \(paused ? "pause" : "resume") screen memory: \(error.localizedDescription)")
        }
        await refresh()
    }

    func deleteRecent(minutes: Int) async {
        await delete(minutes: minutes, label: "from the last \(minutes) minutes")
    }

    func deleteAll() async {
        let alert = NSAlert()
        alert.messageText = "Delete all screen memory?"
        alert.informativeText = "Every saved capture is removed. This can't be undone."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Delete All")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        await delete(minutes: nil, label: "")
    }

    private func delete(minutes: Int?, label: String) async {
        do {
            let n = try await client.deleteScreenMemory(minutes: minutes)
            let what = n == 1 ? "1 capture" : "\(n) captures"
            onStatus?(label.isEmpty ? "Deleted \(what)" : "Deleted \(what) \(label)")
        } catch {
            onStatus?("Couldn't delete screen memory: \(error.localizedDescription)")
        }
        await refresh()
    }
}

/// The menu bar section; hidden while screen memory is off.
struct ScreenMemoryMenu: View {
    @ObservedObject var controller: ScreenMemoryController

    var body: some View {
        if controller.status.enabled {
            Text("Screen memory").font(.caption2).foregroundStyle(.secondary)
            Text(controller.status.summary).font(.caption).lineLimit(2)
            HStack {
                Button(controller.status.paused ? "Resume" : "Pause") {
                    Task { await controller.setPaused(!controller.status.paused) }
                }
                Button("Delete last hour") { Task { await controller.deleteRecent(minutes: 60) } }
                Button("Delete all…") { Task { await controller.deleteAll() } }
            }
            .controlSize(.small)
        }
    }
}

/// The menu bar icon: a record mark while taking meeting notes, an eye while screen
/// text is being remembered.
struct MenuBarIcon: View {
    @ObservedObject var screenMemory: ScreenMemoryController
    @ObservedObject var meetings: MeetingRecorder

    var body: some View {
        Image(systemName: symbol).accessibilityLabel(label)
    }

    private var symbol: String {
        if meetings.active != nil { return "record.circle" }
        return screenMemory.status.isRecording ? "eye.circle" : "sparkles"
    }

    private var label: String {
        if meetings.active != nil { return "Aether, taking meeting notes" }
        return screenMemory.status.isRecording ? "Aether, remembering screen text" : "Aether"
    }
}
