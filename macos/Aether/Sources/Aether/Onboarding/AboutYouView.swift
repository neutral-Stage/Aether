import SwiftUI

struct OnboardingQuestion: Identifiable, Equatable {
    let id: String
    let question: String
    let placeholder: String
}

struct TourExample: Identifiable, Equatable {
    var id: String { say }
    let say: String
    let kind: String      // do | talk | guide
    let app: String

    static func parse(_ obj: [String: Any]) -> TourExample? {
        guard let say = obj["say"] as? String, !say.isEmpty else { return nil }
        return TourExample(say: say, kind: obj["kind"] as? String ?? "do",
                           app: obj["app"] as? String ?? "")
    }
}

/// A few questions about the owner's work (stored in Aether's memory, so every
/// request has that context) and things to try with the apps on this Mac.
struct AboutYouView: View {
    let client: OrchestratorClient

    @State private var questions: [OnboardingQuestion] = []
    @State private var answers: [String: String] = [:]
    @State private var examples: [TourExample] = []
    @State private var memoryEnabled = true
    @State private var status = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("A few answers help Aether fit your work. They stay on this Mac, in its memory.")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(questions) { q in
                VStack(alignment: .leading, spacing: 3) {
                    Text(q.question).font(.caption.weight(.semibold))
                    TextField(q.placeholder, text: Binding(get: { answers[q.id] ?? "" },
                                                           set: { answers[q.id] = $0 }))
                        .textFieldStyle(.roundedBorder)
                }
            }
            HStack {
                Button(saving ? "Saving…" : "Save answers") { save() }
                    .disabled(saving || !memoryEnabled || questions.isEmpty)
                if !status.isEmpty {
                    Text(status).font(.caption2).foregroundStyle(.secondary)
                }
            }
            if !memoryEnabled {
                Text("Memory is off (memory.enabled in config.yaml), so answers can't be kept.")
                    .font(.caption2).foregroundStyle(.orange)
            }
            if !examples.isEmpty {
                Text("Things to try").font(.caption.weight(.semibold)).padding(.top, 6)
                ForEach(examples) { e in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: icon(e.kind)).foregroundStyle(.secondary)
                        Text(e.say).font(.caption)
                        if !e.app.isEmpty {
                            Text(e.app).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
        .task { await load() }
    }

    private func icon(_ kind: String) -> String {
        switch kind {
        case "talk": return "hand.point.up.left"
        case "guide": return "figure.walk"
        default: return "sparkles"
        }
    }

    private func load() async {
        if let data = await client.fetchOnboardingQuestions() {
            questions = data.questions
            answers = data.answers
            memoryEnabled = data.memoryEnabled
        }
        examples = await client.fetchTour()
    }

    private func save() {
        saving = true
        let payload = Dictionary(uniqueKeysWithValues: questions.map { ($0.id, answers[$0.id] ?? "") })
        Task {
            status = await client.saveProfile(payload)
            saving = false
        }
    }
}
