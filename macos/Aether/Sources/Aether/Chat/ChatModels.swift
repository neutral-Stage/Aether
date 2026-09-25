import Foundation

/// One conversation in the sidebar (GET /sessions).
struct ChatSessionSummary: Identifiable, Equatable {
    let id: String
    var title: String
    var updatedAt: Date
    var turns: Int

    static func parse(_ obj: [String: Any]) -> ChatSessionSummary? {
        guard let id = obj["id"] as? String else { return nil }
        let updated = (obj["updated_at"] as? Double) ?? (obj["created_at"] as? Double) ?? 0
        return ChatSessionSummary(id: id, title: obj["title"] as? String ?? "Conversation",
                                  updatedAt: Date(timeIntervalSince1970: updated),
                                  turns: obj["turns"] as? Int ?? 0)
    }
}

/// One action the agent took, shown under its reply.
struct ChatStep: Identifiable, Equatable {
    enum State: Equatable { case running, done, failed }

    let id: Int
    var tool: String
    var description: String
    var state: State = .running
    var summary = ""
    var screenshots: [String] = []
}

struct ChatMessage: Identifiable, Equatable {
    enum Role: Equatable { case user, assistant, note }
    enum Status: Equatable { case working, done, failed, stopped }

    let id = UUID()
    var role: Role
    var text: String
    var status: Status = .done
    var steps: [ChatStep] = []
    /// For a reply: the request it answers (Retry sends it again).
    var goal = ""
    /// What the model is saying while it works (streamed), per step.
    var narration = ""
    var narrationStep = -1
    /// Waiting on the user: a confirmation or a question (the panel asks it).
    var waitingOn = ""
}

/// The conversation shown in the chat window. A pure reducer over the run's
/// events, so it is unit-tested without a sidecar.
struct ChatTranscript: Equatable {
    private(set) var messages: [ChatMessage] = []

    var isWorking: Bool { messages.contains { $0.status == .working } }

    mutating func send(_ goal: String) {
        messages.append(ChatMessage(role: .user, text: goal))
        messages.append(ChatMessage(role: .assistant, text: "", status: .working, goal: goal))
    }

    mutating func note(_ text: String) {
        messages.append(ChatMessage(role: .note, text: text))
    }

    mutating func apply(_ event: SidecarEvent) {
        guard let i = messages.lastIndex(where: { $0.role == .assistant && $0.status == .working })
        else { return }
        switch event {
        case let .token(step, text):
            if step != messages[i].narrationStep {
                messages[i].narrationStep = step
                messages[i].narration = ""
            }
            messages[i].narration += text
        case .step(let payload):
            applyStep(payload, at: i)
        case let .confirmRequest(_, description):
            messages[i].waitingOn = "Waiting for your OK: \(description)"
        case let .question(_, question, _):
            messages[i].waitingOn = "Waiting for your answer: \(question)"
        case let .done(result, _):
            finish(i, text: result, status: .done)
        case .error(let message):
            finish(i, text: message, status: .failed)
        case .stopped:
            finish(i, text: messages[i].text.isEmpty ? "Stopped." : messages[i].text,
                   status: .stopped)
        default:
            break
        }
    }

    /// Rebuild from a stored conversation (GET /sessions/{id}).
    mutating func load(turns: [[String: Any]]) {
        messages = []
        for turn in turns {
            let goal = turn["goal"] as? String ?? ""
            messages.append(ChatMessage(role: .user, text: goal))
            let actions = turn["actions"] as? [String] ?? []
            let steps = actions.enumerated().map {
                ChatStep(id: $0.offset, tool: "", description: $0.element, state: .done)
            }
            let failed = (turn["status"] as? String) == "error"
            messages.append(ChatMessage(role: .assistant, text: turn["result"] as? String ?? "",
                                        status: failed ? .failed : .done, steps: steps, goal: goal))
        }
    }

    private mutating func applyStep(_ p: [String: Any], at i: Int) {
        let step = p["step"] as? Int ?? messages[i].steps.count
        switch p["type"] as? String {
        case "tool_call":
            messages[i].waitingOn = ""
            messages[i].steps.append(ChatStep(id: messages[i].steps.count,
                                              tool: p["tool"] as? String ?? "",
                                              description: p["description"] as? String ?? ""))
        case "tool_result":
            let tool = p["tool"] as? String ?? ""
            if let s = messages[i].steps.lastIndex(where: { $0.tool == tool && $0.state == .running }) {
                let ok = p["ok"] as? Bool ?? true
                messages[i].steps[s].state = ok ? .done : .failed
                messages[i].steps[s].summary = p["summary"] as? String ?? ""
            }
        case "screenshot":
            if let path = p["path"] as? String, let s = messages[i].steps.indices.last {
                messages[i].steps[s].screenshots.append(path)
            }
        case "text":
            messages[i].narrationStep = step
            messages[i].narration = p["text"] as? String ?? ""
        case "plan":
            if let steps = p["steps"] as? [String], !steps.isEmpty {
                messages[i].narration = "Plan: " + steps.joined(separator: " → ")
            }
        default:
            break
        }
    }

    private mutating func finish(_ i: Int, text: String, status: ChatMessage.Status) {
        messages[i].text = text
        messages[i].status = status
        messages[i].waitingOn = ""
        for s in messages[i].steps.indices where messages[i].steps[s].state == .running {
            messages[i].steps[s].state = status == .done ? .done : .failed
        }
    }
}
