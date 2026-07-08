import SwiftUI

/// Live view of multi-agent task graphs (Phase 8): each graph's nodes, their
/// status, dependencies, and the synthesized integration branch.
struct GraphView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var graphs: [OrchestratorClient.TaskGraph] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Task graphs")
                    .font(.caption.weight(.semibold))
                Spacer()
                Button("Refresh") { Task { await load() } }
                    .font(.caption2)
            }
            if graphs.isEmpty {
                Text("No task graphs. Ask Aether to break a goal into parallel agents.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            ForEach(graphs) { graph in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(graph.goal.isEmpty ? graph.graph_id : graph.goal)
                            .font(.caption.weight(.medium))
                            .lineLimit(1)
                        Spacer()
                        Text(graph.status)
                            .font(.caption2)
                            .foregroundStyle(color(for: graph.status))
                    }
                    ForEach(graph.nodes) { node in
                        HStack(spacing: 6) {
                            Circle().fill(color(for: node.status)).frame(width: 6, height: 6)
                            Text(node.title).font(.caption2)
                            if !node.depends_on.isEmpty {
                                Text("⇐ \(node.depends_on.joined(separator: ","))")
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                            Spacer()
                            Text(node.status).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    if let branch = graph.integration_branch, !branch.isEmpty {
                        Text("→ \(branch)").font(.caption2).foregroundStyle(.green)
                    }
                }
                .padding(6)
                .background(Color.secondary.opacity(0.06))
                .cornerRadius(6)
            }
        }
        .task { await load() }
    }

    private func load() async {
        graphs = await client.fetchGraphs()
    }

    private func color(for status: String) -> Color {
        switch status {
        case "done": return .green
        case "running": return .blue
        case "failed": return .red
        case "skipped": return .orange
        default: return .secondary
        }
    }
}
