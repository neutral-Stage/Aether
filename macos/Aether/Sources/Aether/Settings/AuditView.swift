import SwiftUI

/// One entry of the signed audit log (GET /audit).
struct AuditEntry: Identifiable, Equatable {
    let id: String
    let time: Date
    let event: String
    let tool: String
    let summary: String
    let confirmed: Bool?
    let runId: String

    static func parse(_ o: [String: Any]) -> AuditEntry? {
        guard let id = o["id"] as? String, let ts = o["ts"] as? Double,
              let event = o["event"] as? String else { return nil }
        return AuditEntry(id: id, time: Date(timeIntervalSince1970: ts), event: event,
                          tool: o["tool"] as? String ?? "", summary: o["summary"] as? String ?? "",
                          confirmed: o["confirmed"] as? Bool, runId: o["run_id"] as? String ?? "")
    }

    /// A short line for the list: what happened, and the user's answer when there was one.
    var headline: String {
        var parts = [event.replacingOccurrences(of: "_", with: " ")]
        if !tool.isEmpty { parts.append(tool) }
        if let confirmed { parts.append(confirmed ? "approved" : "declined") }
        return parts.joined(separator: " · ")
    }

    var symbol: String {
        switch event {
        case "confirmation": return confirmed == false ? "hand.raised" : "checkmark.shield"
        case "action", "mcp_tool_call": return "cursorarrow.click"
        case "run_start": return "play.circle"
        case "stop", "run_stopped": return "stop.circle"
        default: return "doc.text"
        }
    }
}

/// The result of checking the log's signatures and chain (GET /audit/verify).
struct AuditVerdict: Equatable {
    let ok: Bool
    let message: String
}

/// The activity log: what Aether did, what it asked, and a check that nothing was changed.
struct AuditView: View {
    let client: OrchestratorClient
    @State private var entries: [AuditEntry] = []
    @State private var query = ""
    @State private var onlyConfirmations = false
    @State private var verdict: AuditVerdict?
    @State private var verifying = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("Search the log", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await load() } }
                Toggle("Confirmations only", isOn: $onlyConfirmations)
                    .toggleStyle(.checkbox)
                    .onChange(of: onlyConfirmations) { _, _ in Task { await load() } }
                Button("Refresh") { Task { await load() } }
            }
            HStack {
                Button(verifying ? "Checking…" : "Verify log") { Task { await verify() } }
                    .disabled(verifying)
                    .help("Checks every entry's signature and that none was removed or reordered.")
                if let verdict {
                    Label(verdict.ok ? "Intact: \(verdict.message)" : "Problem: \(verdict.message)",
                          systemImage: verdict.ok ? "checkmark.seal" : "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(verdict.ok ? .green : .red)
                }
            }
            List(entries) { e in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: e.symbol).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(e.headline).font(.caption.bold())
                            Spacer()
                            Text(e.time, format: .dateTime.month().day().hour().minute().second())
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if !e.summary.isEmpty {
                            Text(e.summary).font(.caption).lineLimit(3).textSelection(.enabled)
                        }
                    }
                }
            }
            .frame(minHeight: 220)
        }
        .task { await load() }
    }

    private func load() async {
        entries = await client.fetchAudit(query: query,
                                          event: onlyConfirmations ? "confirmation" : "")
    }

    private func verify() async {
        verifying = true
        verdict = await client.verifyAudit()
            ?? AuditVerdict(ok: false, message: "the sidecar could not be reached")
        verifying = false
    }
}
