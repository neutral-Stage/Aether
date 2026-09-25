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
