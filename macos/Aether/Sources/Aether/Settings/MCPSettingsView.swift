import SwiftUI

/// MCP server settings panel (Phase 10, FR-20).
struct MCPSettingsView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var config = MCPConfig()
    @State private var isLoading = false
    @State private var reloadMessage = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("MCP Servers")
                    .font(.caption.weight(.semibold))
                Spacer()
                if isLoading {
                    ProgressView().controlSize(.small)
                }
                Button("Reload") {
                    Task { await reloadMCP() }
                }
                .font(.caption)
                .disabled(isLoading)
            }

            if !config.enabled {
                Text("MCP disabled in config.yaml — set mcp.enabled: true")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else if config.servers.isEmpty {
                Text("No MCP servers configured")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(config.servers) { server in
                    HStack(spacing: 8) {
                        Circle()
                            .fill(statusColor(server.status))
                            .frame(width: 8, height: 8)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(server.name)
                                .font(.caption)
                            Text(serverSubtitle(server))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer()
                        Text(server.status)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if !reloadMessage.isEmpty {
                Text(reloadMessage)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            } else if !config.reloadNote.isEmpty {
                Text(config.reloadNote)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
        }
        .task { await refresh() }
    }

    private func serverSubtitle(_ server: MCPServerStatus) -> String {
        if server.transport == "sse", let url = server.url {
            return "SSE · \(url)"
        }
        if let cmd = server.command {
            return "stdio · \(cmd)"
        }
        return server.transport
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "connected": return .green
        case "error": return .red
        case "pending": return .orange
        default: return .gray
        }
    }

    private func refresh() async {
        isLoading = true
        defer { isLoading = false }
        if let cfg = await client.fetchMCPConfig() {
            config = cfg
        }
    }

    private func reloadMCP() async {
        isLoading = true
        reloadMessage = await client.reloadMCP()
        await refresh()
        isLoading = false
    }
}
