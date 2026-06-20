import Foundation

struct WorldSnapshot: Codable, Equatable {
    var goal: String?
    var plan: [String]?
    var currentStep: String?
    var frontmostApp: String?
    var elementCount: Int?
    var screenSummary: String?
    var lastScreenshot: String?
    var transcript: String?
    var lastAction: String?
    var status: String?
    var stepFailureCount: Int?
    var axInsufficient: Bool?
    var needsReplan: Bool?
    var recentHistory: [String]?

    enum CodingKeys: String, CodingKey {
        case goal, plan, transcript, status
        case currentStep = "current_step"
        case frontmostApp = "frontmost_app"
        case elementCount = "element_count"
        case screenSummary = "screen_summary"
        case lastScreenshot = "last_screenshot"
        case lastAction = "last_action"
        case stepFailureCount = "step_failure_count"
        case axInsufficient = "ax_insufficient"
        case needsReplan = "needs_replan"
        case recentHistory = "recent_history"
    }
}

@MainActor
final class WorldModel: ObservableObject {
    @Published var goal: String = ""
    @Published var currentStep: String = ""
    @Published var lastAction: String = ""
    @Published var status: String = "idle"
    @Published var frontmostApp: String = ""
    @Published var transcript: String = ""
    @Published var snapshot: WorldSnapshot?

    func apply(hud: [String: Any]) {
        if let g = hud["goal"] as? String { goal = g }
        if let s = hud["step"] as? String { currentStep = s }
        if let la = hud["last_action"] as? String { lastAction = la }
        if let st = hud["status"] as? String { status = st }
        if let tr = hud["transcript"] as? String { transcript = tr }
    }

    func apply(world: WorldSnapshot?) {
        guard let world else { return }
        snapshot = world
        if let g = world.goal { goal = g }
        if let s = world.currentStep { currentStep = s }
        if let la = world.lastAction { lastAction = la }
        if let st = world.status { status = st }
        if let app = world.frontmostApp { frontmostApp = app }
    }

    func reset() {
        goal = ""
        currentStep = ""
        lastAction = ""
        status = "idle"
        frontmostApp = ""
        transcript = ""
        snapshot = nil
    }
}
