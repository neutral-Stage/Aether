import Foundation

/// MCP server entry from sidecar `GET /config/mcp`.
struct MCPServerStatus: Identifiable, Equatable {
    var id: String { name }
    var name: String
    var enabled: Bool
    var transport: String
    var status: String
    var url: String?
    var command: String?
}

struct MCPConfig: Equatable {
    var enabled: Bool = false
    var servers: [MCPServerStatus] = []
    var reloadNote: String = ""
}
