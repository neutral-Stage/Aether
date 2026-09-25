import AppKit
import XCTest
@testable import Aether

final class AetherConfigTests: XCTestCase {
    func testSidecarURL() {
        XCTAssertEqual(AetherConfig.sidecarPort, 8765)
        XCTAssertTrue(AetherConfig.sidecarBaseURL.absoluteString.contains("8765"))
    }

    func testCommandBarHotkey() {
        XCTAssertTrue(AetherConfig.commandBarModifiers.contains(.option))
        XCTAssertEqual(AetherConfig.commandBarKeyCode, 49)
    }

    func testNativeEffectorPort() {
        XCTAssertEqual(AetherConfig.nativeEffectorPort, 8766)
    }

    func testAppcastDetection() {
        XCTAssertTrue(
            AetherConfig.looksLikeAppcast(URL(string: "https://example.com/appcast.xml")!)
        )
        XCTAssertFalse(
            AetherConfig.looksLikeAppcast(URL(string: "https://api.github.com/repos/x/releases/latest")!)
        )
    }

    func testAuditKeychainRoundTrip() {
        defer { _ = AuditKeychain.deleteKey() }
        let sample = Data("unit-test-audit-hmac-key-material".utf8)
        XCTAssertTrue(AuditKeychain.saveKey(sample))
        XCTAssertEqual(AuditKeychain.loadKey(), sample)
    }
}

final class InputControllerTests: XCTestCase {
    func testUTF16ChunksKeepSurrogatePairsTogether() {
        let chunks = InputController.utf16Chunks("a😀é")
        XCTAssertEqual(chunks.count, 3)
        XCTAssertEqual(chunks[0], [0x61])
        XCTAssertEqual(chunks[1].count, 2)  // 😀 is a surrogate pair
        XCTAssertEqual(chunks[2].count, 1)
    }

    func testUTF16ChunksEmpty() {
        XCTAssertTrue(InputController.utf16Chunks("").isEmpty)
    }
}

final class ScreenCaptureSizingTests: XCTestCase {
    func testNativeSizeOnRetina() {
        let size = ScreenCapture.targetPixelSize(pointWidth: 1512, pointHeight: 982, scale: 2, maxEdge: 0)
        XCTAssertEqual(size.width, 3024)
        XCTAssertEqual(size.height, 1964)
    }

    func testLongEdgeCapKeepsAspect() {
        let size = ScreenCapture.targetPixelSize(pointWidth: 1512, pointHeight: 982, scale: 2, maxEdge: 1600)
        XCTAssertEqual(size.width, 1600)
        XCTAssertEqual(size.height, 1039)
    }

    func testCapIgnoredWhenSmaller() {
        let size = ScreenCapture.targetPixelSize(pointWidth: 1280, pointHeight: 800, scale: 1, maxEdge: 1600)
        XCTAssertEqual(size.width, 1280)
        XCTAssertEqual(size.height, 800)
    }
}

final class DoctorReportTests: XCTestCase {
    func testParse() {
        let json = #"{"verdict":"warn","checks":[{"name":"git","status":"ok","detail":"present","fix":""},{"name":"Default brain","status":"fail","detail":"ZAI_API_KEY missing","fix":"add it"},{"bad":1}]}"#
        let report = DoctorReport.parse(Data(json.utf8))
        XCTAssertEqual(report?.verdict, "warn")
        XCTAssertEqual(report?.checks.count, 2)
        XCTAssertEqual(report?.checks[1].fix, "add it")
    }

    func testParseRejectsGarbage() {
        XCTAssertNil(DoctorReport.parse(Data("nope".utf8)))
    }
}

final class SidecarEventParseTests: XCTestCase {
    func testQuestionNeedsRequestId() {
        XCTAssertTrue(SidecarEvent.parse(["type": "question", "question": "Which?"], fallbackGoal: "").isEmpty)
        let events = SidecarEvent.parse(
            ["type": "question", "request_id": "r1", "question": "Which?", "options": ["a", "b"]],
            fallbackGoal: "")
        guard case let .question(rid, question, options)? = events.first else {
            return XCTFail("no question event")
        }
        XCTAssertEqual(rid, "r1")
        XCTAssertEqual(question, "Which?")
        XCTAssertEqual(options, ["a", "b"])
    }

    func testRunStartAndDoneCarryTheSession() {
        let start = SidecarEvent.parse(["type": "run_start", "run_id": "x", "session_id": "s1"],
                                       fallbackGoal: "the goal")
        XCTAssertEqual(start.count, 2)
        guard case .session(let sid) = start[0] else { return XCTFail("no session") }
        XCTAssertEqual(sid, "s1")
        guard case let .runStart(runId, goal) = start[1] else { return XCTFail("no run_start") }
        XCTAssertEqual(runId, "x")
        XCTAssertEqual(goal, "the goal")
        let done = SidecarEvent.parse(["type": "done", "result": "ok", "session_id": "s1"],
                                      fallbackGoal: "")
        guard case .done(let result, _) = done[1] else { return XCTFail("no done") }
        XCTAssertEqual(result, "ok")
    }

    func testStepsAndUnknownTypes() {
        let events = SidecarEvent.parse(["type": "tool_call", "description": "click 'Save'"],
                                        fallbackGoal: "")
        guard case .step(let obj)? = events.first else { return XCTFail("no step") }
        XCTAssertEqual(obj["description"] as? String, "click 'Save'")
        XCTAssertTrue(SidecarEvent.parse(["type": "mystery"], fallbackGoal: "").isEmpty)
        XCTAssertTrue(SidecarEvent.parse([:], fallbackGoal: "").isEmpty)
    }
}

final class OverlayGeometryTests: XCTestCase {
    func testCoordinateConversionRoundTrips() {
        let p = CGPoint(x: 100, y: 50)
        let ak = OverlayGeometry.appKit(p, primaryHeight: 900)
        XCTAssertEqual(ak, CGPoint(x: 100, y: 850))
        XCTAssertEqual(OverlayGeometry.topLeft(ak, primaryHeight: 900), p)
        let r = OverlayGeometry.appKitRect(CGRect(x: 10, y: 20, width: 30, height: 40), primaryHeight: 900)
        XCTAssertEqual(r, CGRect(x: 10, y: 840, width: 30, height: 40))
    }

    func testFlightTimingAndShape() {
        XCTAssertEqual(OverlayGeometry.flightDuration(distance: 100), 0.6)
        XCTAssertEqual(OverlayGeometry.flightDuration(distance: 800), 1.0)
        XCTAssertEqual(OverlayGeometry.flightDuration(distance: 5000), 1.4)
        let a = CGPoint(x: 0, y: 0), b = CGPoint(x: 1000, y: 0)
        let c = OverlayGeometry.controlPoint(from: a, to: b)
        XCTAssertEqual(c.x, 500, accuracy: 0.001)
        XCTAssertEqual(c.y, 80, accuracy: 0.001)             // lift capped at 80, bowing up
        XCTAssertEqual(OverlayGeometry.smoothstep(0), 0)
        XCTAssertEqual(OverlayGeometry.smoothstep(1), 1)
        XCTAssertEqual(OverlayGeometry.smoothstep(0.5), 0.5, accuracy: 1e-9)
        XCTAssertEqual(OverlayGeometry.swell(0.5), 1.3, accuracy: 1e-9)
        let over = OverlayGeometry.overshoot(from: a, to: b)
        XCTAssertEqual(over, CGPoint(x: 1012, y: 0))           // min(12, 6% of 1000)
        let path = OverlayGeometry.flightPath(from: a, to: b)
        XCTAssertEqual(path.first, a)
        XCTAssertEqual(path.last, b)
        XCTAssertTrue(path.contains { $0.x > 1000 })            // overshoots, then settles
    }

    func testRingNeverTooSmall() {
        let ring = OverlayGeometry.ringRect(around: CGRect(x: 100, y: 100, width: 4, height: 4))
        XCTAssertGreaterThanOrEqual(ring.width, 36)
        XCTAssertGreaterThanOrEqual(ring.height, 36)
        XCTAssertEqual(ring.midX, 102, accuracy: 0.001)
        XCTAssertEqual(OverlayGeometry.screenIndex(for: CGPoint(x: 1500, y: 10),
                                                   frames: [CGRect(x: 0, y: 0, width: 1440, height: 900),
                                                            CGRect(x: 1440, y: 0, width: 1920, height: 1080)]), 1)
    }

    func testTargetFromJSON() {
        let t = OverlayTarget(json: ["kind": "rect", "x": 10, "y": 20, "w": 30, "h": 40,
                                     "label": "Save", "points": [[1, 2], [3, 4]]])
        XCTAssertEqual(t?.kind, .rect)
        XCTAssertEqual(t?.frame, CGRect(x: -5, y: 0, width: 30, height: 40))
        XCTAssertEqual(t?.points, [CGPoint(x: 1, y: 2), CGPoint(x: 3, y: 4)])
        XCTAssertNil(OverlayTarget(json: ["kind": "point"]))
        XCTAssertEqual(OverlayTarget(json: ["x": 5, "y": 5])?.frame.width, 28)
    }

    func testTalkReplyAndPointerEvents() {
        let data = #"{"answer": "Here.", "targets": [{"kind": "point", "x": 1, "y": 2, "label": "A"}], "session_id": "s"}"#
            .data(using: .utf8)!
        let reply = TalkReply.parse(data)
        XCTAssertEqual(reply?.answer, "Here.")
        XCTAssertEqual(reply?.targets.first?.label, "A")
        XCTAssertEqual(reply?.sessionId, "s")
        XCTAssertNil(TalkReply.parse(Data("[]".utf8)))
        let events = SidecarEvent.parse(["type": "pointer", "targets": [["x": 3, "y": 4]]], fallbackGoal: "")
        guard case .pointer(let targets)? = events.first else { return XCTFail("no pointer") }
        XCTAssertEqual(targets.first?.center, CGPoint(x: 3, y: 4))
        XCTAssertTrue(SidecarEvent.parse(["type": "pointer", "targets": []], fallbackGoal: "").isEmpty)
    }

    func testTalkChordMustBeExact() {
        let want: NSEvent.ModifierFlags = [.control, .option]
        XCTAssertTrue(ModifierHoldController.isExactly([.control, .option], want))
        XCTAssertTrue(ModifierHoldController.isExactly([.control, .option, .capsLock], want))
        XCTAssertFalse(ModifierHoldController.isExactly([.control, .option, .command], want))
        XCTAssertFalse(ModifierHoldController.isExactly([.control], want))
    }
}

final class GuideIntentTests: XCTestCase {
    func testGuideRequests() {
        XCTAssertTrue(GuideIntent.isGuideRequest("Show me how to add a printer"))
        XCTAssertTrue(GuideIntent.isGuideRequest("hey aether, teach me to split the screen"))
        XCTAssertTrue(GuideIntent.isGuideRequest("How do I turn on dark mode?"))
        XCTAssertFalse(GuideIntent.isGuideRequest("show me my downloads"))
        XCTAssertFalse(GuideIntent.isGuideRequest("open Safari"))
    }

    func testSpokenControls() {
        XCTAssertEqual(GuideIntent.control(for: "Next."), "next")
        XCTAssertEqual(GuideIntent.control(for: "go back"), "back")
        XCTAssertEqual(GuideIntent.control(for: "say that again"), "repeat")
        XCTAssertEqual(GuideIntent.control(for: "Can you do it for me?"), "do_it")
        XCTAssertEqual(GuideIntent.control(for: "stop"), "stop")
        XCTAssertNil(GuideIntent.control(for: "what's the weather"))
    }

    func testGuideEvents() {
        let step = SidecarEvent.parse(["type": "guide_step", "guide_id": "g1", "index": 1, "total": 3,
                                       "say": "Click Add", "target": ["x": 10, "y": 20, "label": "Add"]],
                                      fallbackGoal: "")
        guard case let .guideStep(id, index, total, say, target)? = step.first else {
            return XCTFail("no guide step")
        }
        XCTAssertEqual(id, "g1")
        XCTAssertEqual(index, 1)
        XCTAssertEqual(total, 3)
        XCTAssertEqual(say, "Click Add")
        XCTAssertEqual(target?.label, "Add")
        let done = SidecarEvent.parse(["type": "guide_done", "guide_id": "g1", "status": "stopped"],
                                      fallbackGoal: "")
        guard case let .guideDone(doneId, status)? = done.first else { return XCTFail("no done") }
        XCTAssertEqual(doneId, "g1")
        XCTAssertEqual(status, "stopped")
    }
}

final class StreamingSpeechTests: XCTestCase {
    func testClausesComeOutAsSoonAsTheyEnd() {
        var s = ClauseSplitter()
        XCTAssertEqual(s.feed("Open System Set"), [])
        XCTAssertEqual(s.feed("tings. Then click"), ["Open System Settings."])
        XCTAssertEqual(s.feed(" Bluetooth!\nIt is on the left"), ["Then click Bluetooth!"])
        XCTAssertEqual(s.flush(), "It is on the left")
        XCTAssertNil(s.flush())
    }

    func testCommasSplitOnlyLongClausesAndDecimalsStayWhole() {
        var s = ClauseSplitter()
        XCTAssertEqual(s.feed("Yes, it is. "), ["Yes, it is."])
        let long = "The volume slider sits in the Control Centre at the top right, "
        XCTAssertEqual(s.feed(long + "next to Wi-Fi"),
                       ["The volume slider sits in the Control Centre at the top right,"])
        XCTAssertEqual(s.feed(" 2.5 GB free"), [])
        XCTAssertEqual(s.flush(), "next to Wi-Fi 2.5 GB free")
    }

    func testTokenEvents() {
        let tok = SidecarEvent.parse(["type": "talk_token", "talk_id": "t1", "text": "Hi "],
                                     fallbackGoal: "")
        guard case let .talkToken(id, text)? = tok.first else { return XCTFail("no talk token") }
        XCTAssertEqual(id, "t1")
        XCTAssertEqual(text, "Hi ")
        let done = SidecarEvent.parse(["type": "talk_done", "talk_id": "t1", "answer": "Hi"],
                                      fallbackGoal: "")
        guard case let .talkDone(doneId, answer)? = done.first else { return XCTFail("no done") }
        XCTAssertEqual(doneId, "t1")
        XCTAssertEqual(answer, "Hi")
        let run = SidecarEvent.parse(["type": "token", "step": 3, "text": "Opening"],
                                     fallbackGoal: "")
        guard case let .token(step, runText)? = run.first else { return XCTFail("no token") }
        XCTAssertEqual(step, 3)
        XCTAssertEqual(runText, "Opening")
        XCTAssertTrue(SidecarEvent.parse(["type": "talk_token", "text": "x"], fallbackGoal: "")
            .isEmpty)
    }
}
