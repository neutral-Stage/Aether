import SwiftUI

/// Learned skill from sidecar (Phase 11, FR-25).
struct SkillSummary: Identifiable {
    let id: Int
    let name: String
    let description: String
    let goalPattern: String
    let parameters: [String]
    let steps: [[String: Any]]
    let successCount: Int

    init?(dict: [String: Any]) {
        guard let id = dict["id"] as? Int,
              let name = dict["name"] as? String else { return nil }
        self.id = id
        self.name = name
        description = dict["description"] as? String ?? ""
        goalPattern = dict["goal_pattern"] as? String ?? ""
        parameters = dict["parameters"] as? [String] ?? []
        steps = dict["steps"] as? [[String: Any]] ?? []
        successCount = dict["success_count"] as? Int ?? 0
    }
}

/// Skill review + parameterized replay UI (Phase 11, FR-25).
struct SkillsView: View {
    @ObservedObject var client: OrchestratorClient
    @State private var skills: [SkillSummary] = []
    @State private var selectedId: Int?
    @State private var paramValues: [String: String] = [:]
    @State private var isLoading = false
    @State private var statusMessage = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Learned Skills")
                    .font(.caption.weight(.semibold))
                Spacer()
                if isLoading {
                    ProgressView().controlSize(.small)
                }
                Button("Refresh") {
                    Task { await loadSkills() }
                }
                .font(.caption)
            }

            if skills.isEmpty && !isLoading {
                Text("No skills yet — successful runs distill skills automatically.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Picker("Skill", selection: $selectedId) {
                    Text("Select…").tag(Optional<Int>.none)
                    ForEach(skills) { skill in
                        Text("\(skill.name) (\(skill.successCount)×)")
                            .tag(Optional(skill.id))
                    }
                }
                .labelsHidden()

                if let skill = skills.first(where: { $0.id == selectedId }) {
                    Text(skill.description)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)

                    if !skill.parameters.isEmpty {
                        Text("Parameters")
                            .font(.caption2.weight(.medium))
                        ForEach(Array(skill.parameters.enumerated()), id: \.offset) { idx, param in
                            let key = "param\(idx + 1)"
                            HStack {
                                Text(param)
                                    .font(.caption2)
                                    .lineLimit(1)
                                TextField("value", text: binding(for: key, default: param))
                                    .textFieldStyle(.roundedBorder)
                                    .font(.caption2)
                            }
                        }
                    }

                    Text("Steps")
                        .font(.caption2.weight(.medium))
                    ForEach(Array(skill.steps.enumerated()), id: \.offset) { idx, step in
                        let tool = step["tool"] as? String ?? "?"
                        Text("\(idx + 1). \(tool)")
                            .font(.caption2.monospaced())
                            .lineLimit(1)
                    }

                    HStack {
                        Button("Replay (direct)") {
                            Task { await replay(viaOrchestrator: false) }
                        }
                        .font(.caption)
                        .disabled(isLoading)

                        Button("Replay (agent)") {
                            Task { await replay(viaOrchestrator: true) }
                        }
                        .font(.caption)
                        .disabled(isLoading || client.isRunning)
                    }
                }
            }

            if !statusMessage.isEmpty {
                Text(statusMessage)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(3)
            }
        }
        .task { await loadSkills() }
    }

    private func binding(for key: String, default defaultVal: String) -> Binding<String> {
        Binding(
            get: { paramValues[key] ?? defaultVal },
            set: { paramValues[key] = $0 }
        )
    }

    private func loadSkills() async {
        isLoading = true
        defer { isLoading = false }
        do {
            skills = try await client.fetchSkills()
            if selectedId == nil {
                selectedId = skills.first?.id
            }
            statusMessage = "\(skills.count) skill(s) loaded"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func replay(viaOrchestrator: Bool) async {
        guard let id = selectedId else { return }
        isLoading = true
        defer { isLoading = false }
        var args: [String: String] = [:]
        for (k, v) in paramValues where !v.isEmpty {
            args[k] = v
        }
        do {
            let result = try await client.replaySkill(
                id: id,
                args: args,
                viaOrchestrator: viaOrchestrator
            )
            statusMessage = result
        } catch {
            statusMessage = error.localizedDescription
        }
    }
}
