import SwiftUI

/// "What can I do?" — discoverability surface driven by GET /catalog (Phase 7):
/// the apps Aether has expertise in (knowledge packs) + the tools it can use.
struct CatalogView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var catalog: OrchestratorClient.Catalog?
    @State private var loading = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("What Aether can do")
                    .font(.caption.weight(.semibold))
                Spacer()
                Button(loading ? "Loading…" : "Refresh") { Task { await load() } }
                    .font(.caption2)
                    .disabled(loading)
            }

            if let catalog {
                Text("\(catalog.apps.count) apps with expertise · \(catalog.tool_count) tools")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        Text("Apps")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(catalog.apps.map(\.app).joined(separator: " · "))
                            .font(.caption2)
                            .fixedSize(horizontal: false, vertical: true)
                        Divider().padding(.vertical, 2)
                        Text("Try saying things like…")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ForEach(Self.examples, id: \.self) { example in
                            Text("“\(example)”")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .frame(maxHeight: 140)
            } else {
                Text("Start the sidecar to see supported apps and tools.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .task { await load() }
    }

    private func load() async {
        loading = true
        catalog = await client.fetchCatalog()
        loading = false
    }

    static let examples = [
        "Open Safari and go to apple.com",
        "Draft an email to Alex about the launch",
        "Spawn a Claude Code agent to fix the failing tests",
        "Watch Xcode and tell me when the build finishes",
        "Summarize the PDF that's open in Preview",
    ]
}
