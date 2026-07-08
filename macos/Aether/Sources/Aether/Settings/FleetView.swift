import SwiftUI

/// Fleet agent session from sidecar GET /fleet (Phase 2).
struct FleetSession: Identifiable {
    let id: String
    let label: String
    let agentType: String
    let state: String
    let elapsedSec: Double
    let costUsd: Double
    let lastOutput: String
    let resultSummary: String

    init?(dict: [String: Any]) {
        guard let id = dict["session_id"] as? String else { return nil }
        self.id = id
        label = dict["label"] as? String ?? id
        agentType = dict["agent_type"] as? String ?? "?"
        state = dict["state"] as? String ?? "?"
        elapsedSec = dict["elapsed_sec"] as? Double ?? 0
        costUsd = dict["cost_usd"] as? Double ?? 0
        lastOutput = dict["last_output"] as? String ?? ""
        resultSummary = dict["result_summary"] as? String ?? ""
    }

    var isActive: Bool {
        ["starting", "running", "awaiting_input"].contains(state)
    }
}

/// Agent fleet monitor: session list with steer/stop controls (Phase 2).
struct FleetView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var sessions: [FleetSession] = []
    @State private var sendTexts: [String: String] = [:]
    @State private var statusMessage = ""

    private let pollTimer = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Agent Fleet")
                    .font(.caption.weight(.semibold))
                Spacer()
                Button("Refresh") { Task { await load() } }
                    .font(.caption)
                if sessions.contains(where: { $0.isActive }) {
                    Button("Stop all", role: .destructive) {
                        Task { await stopAll() }
                    }
                    .font(.caption)
                }
            }

            if sessions.isEmpty {
                Text("No agent sessions — say \"spawn a Claude Code agent to …\"")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            ForEach(sessions) { session in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(stateEmoji(session.state))
                        Text("\(session.label) · \(session.agentType)")
                            .font(.caption.weight(.medium))
                        Spacer()
                        Text(String(format: "%dm · $%.3f", Int(session.elapsedSec / 60), session.costUsd))
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                        if session.isActive {
                            Button("Stop", role: .destructive) {
                                Task { await stop(session.id) }
                            }
                            .font(.caption2)
                        }
                    }
                    let line = session.resultSummary.isEmpty ? session.lastOutput : session.resultSummary
                    if !line.isEmpty {
                        Text(line)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    if session.isActive {
                        HStack {
                            TextField("Steer…", text: binding(for: session.id))
                                .textFieldStyle(.roundedBorder)
                                .font(.caption2)
                            Button("Send") { Task { await send(session.id) } }
                                .font(.caption2)
                                .disabled((sendTexts[session.id] ?? "").isEmpty)
                        }
                    }
                }
                .padding(6)
                .background(.quaternary.opacity(0.3))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }

            if !statusMessage.isEmpty {
                Text(statusMessage)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
        }
        .task { await load() }
        .onReceive(pollTimer) { _ in
            if !sessions.isEmpty || statusMessage.isEmpty {
                Task { await load() }
            }
        }
    }

    private func stateEmoji(_ state: String) -> String {
        switch state {
        case "running", "starting": return "🟢"
        case "awaiting_input": return "🟡"
        case "done": return "✅"
        case "error", "timeout": return "🔴"
        default: return "⚪️"
        }
    }

    private func binding(for id: String) -> Binding<String> {
        Binding(
            get: { sendTexts[id] ?? "" },
            set: { sendTexts[id] = $0 }
        )
    }

    private func load() async {
        do {
            sessions = try await client.fetchFleet()
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func send(_ id: String) async {
        let text = sendTexts[id] ?? ""
        guard !text.isEmpty else { return }
        do {
            let delivered = try await client.sendToAgent(id: id, text: text)
            sendTexts[id] = ""
            statusMessage = delivered ? "Sent." : "Not delivered (session busy or finished)."
            await load()
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func stop(_ id: String) async {
        do {
            try await client.stopAgent(id: id)
            await load()
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func stopAll() async {
        do {
            let count = try await client.stopAllAgents()
            statusMessage = "Stopped \(count) session(s)."
            await load()
        } catch {
            statusMessage = error.localizedDescription
        }
    }
}
