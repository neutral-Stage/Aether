import AppKit
import SwiftUI

/// One row's displayed state — pure function of the sidecar's on/off flag and
/// (for Calendar/Reminders/Contacts) `PIMService`'s EventKit/Contacts status,
/// so it's testable without a view or any macOS permission.
enum IntegrationRowState: Equatable {
    case connected
    case notConnected
    case denied

    var label: String {
        switch self {
        case .connected: return "Connected"
        case .notConnected: return "Not connected"
        case .denied: return "Denied — allow in System Settings"
        }
    }
}

enum IntegrationRow {
    /// `osStatus` is `PIMService.status()`'s value for this app's capability
    /// ("authorized" | "write_only" | "denied" | "restricted" | "not_determined"),
    /// or `nil` for Notes/Mail, which have no EventKit/Contacts status to read.
    static func state(enabled: Bool, osStatus: String?) -> IntegrationRowState {
        if osStatus == "denied" || osStatus == "restricted" {
            return .denied
        }
        return enabled ? .connected : .notConnected
    }
}

/// Calendar, Reminders, Contacts, Notes and Mail — no accounts or tokens.
/// Connecting one just lets its tools run; the macOS permission prompt (or the
/// Automation prompt, for Notes/Mail) is separate and can still say no.
/// Main-actor bound: PIMService and NSAppleScript must run on the main thread.
@MainActor
struct IntegrationsView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var entries: [[String: Any]] = []
    @State private var osStatus: [String: String] = [:]
    @State private var busyId: String?
    @State private var statusLine = ""

    private struct AppRow: Identifiable {
        let id: String
        let icon: String
    }
    private static let apps: [AppRow] = [
        AppRow(id: "calendar", icon: "calendar"),
        AppRow(id: "reminders", icon: "checklist"),
        AppRow(id: "contacts", icon: "person.crop.circle"),
        AppRow(id: "notes", icon: "note.text"),
        AppRow(id: "mail", icon: "envelope"),
    ]
    private static let eventKitIds: Set<String> = ["calendar", "reminders", "contacts"]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No accounts or tokens — Aether reads these through macOS itself. Creating " +
                 "an event, reminder or note always shows an editable draft first, and never " +
                 "sends invitations.")
                .font(.caption2)
                .foregroundStyle(.secondary)
            ForEach(Self.apps) { app in
                if let entry = entries.first(where: { ($0["id"] as? String) == app.id }) {
                    row(for: entry, icon: app.icon)
                    if app.id != Self.apps.last?.id { Divider() }
                }
            }
            if !statusLine.isEmpty {
                Text(statusLine).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .task { await load() }
    }

    @ViewBuilder
    private func row(for entry: [String: Any], icon: String) -> some View {
        let id = entry["id"] as? String ?? ""
        let name = entry["name"] as? String ?? id
        let description = entry["description"] as? String ?? ""
        let enabled = entry["enabled"] as? Bool ?? false
        let state = IntegrationRow.state(enabled: enabled, osStatus: osStatus[id])

        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top) {
                Image(systemName: icon).frame(width: 18)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name).font(.caption.bold())
                    Text(description).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Text(state.label)
                    .font(.caption2)
                    .foregroundStyle(state == .denied ? Color.red
                                     : (state == .connected ? Color.green : Color.secondary))
            }
            HStack {
                if state == .denied || !Self.eventKitIds.contains(id) {
                    Button("Open Privacy Settings") { openPrivacySettings(for: id) }
                        .font(.caption2)
                }
                Spacer()
                Button(busyId == id ? "Working…" : (enabled ? "Disconnect" : "Connect")) {
                    Task { await toggle(id: id, enabled: enabled) }
                }
                .font(.caption2)
                .disabled(busyId != nil)
            }
        }
        .help(enabled
              ? "Disconnecting only stops Aether from using \(name) — it doesn't change the "
                + "macOS permission."
              : description)
    }

    private func load() async {
        entries = await client.fetchIntegrations()
        osStatus = PIMService.shared.status()
    }

    private func toggle(id: String, enabled: Bool) async {
        busyId = id
        defer { busyId = nil }
        if enabled {
            await setEnabled(id, false)
        } else {
            if Self.eventKitIds.contains(id) {
                _ = await PIMService.shared.requestAccess(id)
            } else {
                triggerAutomationPrompt(for: id)
            }
            await setEnabled(id, true)
        }
        await load()
    }

    private func setEnabled(_ id: String, _ on: Bool) async {
        do {
            try await client.setIntegration(id, enabled: on)
        } catch {
            statusLine = "Couldn't \(on ? "connect" : "disconnect"): \(error.localizedDescription)"
        }
    }

    /// A harmless script that touches Notes/Mail so macOS shows the Automation
    /// consent prompt for Aether, the same way the first real use would.
    private func triggerAutomationPrompt(for id: String) {
        let source = id == "notes"
            ? "tell application \"Notes\" to count notes"
            : "tell application \"Mail\" to count mailboxes"
        guard let script = NSAppleScript(source: source) else { return }
        var errorInfo: NSDictionary?
        script.executeAndReturnError(&errorInfo)
    }

    private func openPrivacySettings(for id: String) {
        let suffix: String
        switch id {
        case "calendar": suffix = "Privacy_Calendars"
        case "reminders": suffix = "Privacy_Reminders"
        case "contacts": suffix = "Privacy_Contacts"
        default: suffix = "Privacy_Automation"   // notes, mail
        }
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?\(suffix)") else {
            return
        }
        NSWorkspace.shared.open(url)
    }
}
