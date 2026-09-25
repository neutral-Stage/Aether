import AppKit
import AVFoundation
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

@MainActor
final class ChatTranscriptTests: XCTestCase {
    func testARunBecomesAReplyWithSteps() {
        var t = ChatTranscript()
        t.send("open my downloads")
        XCTAssertTrue(t.isWorking)
        t.apply(.token(step: 1, text: "Opening "))
        t.apply(.token(step: 1, text: "Finder."))
        XCTAssertEqual(t.messages[1].narration, "Opening Finder.")
        t.apply(.step(["type": "tool_call", "step": 1, "tool": "finder_go_to",
                       "description": "Finder → ~/Downloads"]))
        t.apply(.step(["type": "screenshot", "step": 1, "tool": "finder_go_to",
                       "path": "/tmp/s.png"]))
        t.apply(.step(["type": "tool_result", "step": 1, "tool": "finder_go_to", "ok": true,
                       "summary": "ok"]))
        t.apply(.token(step: 2, text: "Done"))
        XCTAssertEqual(t.messages[1].narration, "Done")
        t.apply(.confirmRequest(requestId: "r", description: "delete x", grant: ""))
        XCTAssertEqual(t.messages[1].waitingOn, "Waiting for your OK: delete x")
        t.apply(.done(result: "Your Downloads folder is open.", world: nil))
        let reply = t.messages[1]
        XCTAssertEqual(reply.status, .done)
        XCTAssertEqual(reply.text, "Your Downloads folder is open.")
        XCTAssertEqual(reply.steps.count, 1)
        XCTAssertEqual(reply.steps[0].state, .done)
        XCTAssertEqual(reply.steps[0].screenshots, ["/tmp/s.png"])
        XCTAssertEqual(reply.waitingOn, "")
        XCTAssertFalse(t.isWorking)
        t.apply(.done(result: "ignored", world: nil))      // nothing is working any more
        XCTAssertEqual(t.messages.count, 2)
    }

    func testFailuresStopsAndHistory() {
        var t = ChatTranscript()
        t.send("x")
        t.apply(.step(["type": "tool_call", "tool": "click", "description": "click OK"]))
        t.apply(.stopped)
        XCTAssertEqual(t.messages[1].status, .stopped)
        XCTAssertEqual(t.messages[1].text, "Stopped.")
        XCTAssertEqual(t.messages[1].steps[0].state, .failed)
        XCTAssertEqual(t.messages[1].goal, "x")
        t.load(turns: [["goal": "a", "result": "b", "actions": ["open Safari"], "status": "idle"],
                       ["goal": "c", "result": "boom", "actions": [], "status": "error"]])
        XCTAssertEqual(t.messages.map(\.text), ["a", "b", "c", "boom"])
        XCTAssertEqual(t.messages[1].steps.first?.description, "open Safari")
        XCTAssertEqual(t.messages[3].status, .failed)
        let s = ChatSessionSummary.parse(["id": "s1", "title": "Mail", "updated_at": 10.0,
                                          "turns": 2])
        XCTAssertEqual(s?.title, "Mail")
        XCTAssertNil(ChatSessionSummary.parse(["title": "no id"]))
    }
}

final class WakeCommandTests: XCTestCase {
    func testFindsTheCommandAfterTheWakePhrase() {
        XCTAssertEqual(WakeCommand.command(in: "Hey Aether, open my downloads"), "open my downloads")
        XCTAssertEqual(WakeCommand.command(in: "hey ether what's on my calendar?"),
                       "what's on my calendar")
        XCTAssertEqual(WakeCommand.command(in: "OK Aether"), "")
        XCTAssertNil(WakeCommand.command(in: "open my downloads"))
        XCTAssertNil(WakeCommand.command(in: "the aether is thin"))
        XCTAssertNil(WakeCommand.command(in: "hey either way we should go"))
        // the last wake phrase wins (the recognizer can repeat itself)
        XCTAssertEqual(WakeCommand.command(in: "hey aether stop hey aether mute"), "mute")
    }
}

final class ChipAndSkillTests: XCTestCase {
    func testChipParse() {
        let chip = Chip.parse(["label": "Explain it", "kind": "understand", "prompt": "Explain X"])
        XCTAssertEqual(chip?.symbol, "questionmark.circle")
        XCTAssertNil(Chip.parse(["label": "", "kind": "execute", "prompt": "x"]))
        XCTAssertEqual(Chip.parse(["label": "Do", "kind": "execute", "prompt": "Run"])?.symbol,
                       "play.circle")
    }

    func testQuickSkillParseAndHotkeys() {
        let skill = QuickSkill.parse(["id": "abc123", "name": "Sum", "prompt": "p",
                                      "capture": "clipboard", "destination": "speak",
                                      "hotkey": "3", "file_path": ""])
        XCTAssertEqual(skill?.capture, "clipboard")
        XCTAssertEqual(skill?.json["hotkey"] as? String, "3")
        XCTAssertEqual(QuickSkill.keyCode(for: "1"), 18)
        XCTAssertEqual(QuickSkill.keyCode(for: "9"), 25)
        XCTAssertNil(QuickSkill.keyCode(for: ""))
        XCTAssertNil(QuickSkill.parse(["name": "no id"]))
    }
}

final class ScreenMemoryStatusTests: XCTestCase {
    func testOffUntilTheSidecarSaysOtherwise() {
        let off = ScreenMemoryStatus.parse(["enabled": false])
        XCTAssertFalse(off.isRecording)
        XCTAssertEqual(off.summary, "Screen memory is off")
        XCTAssertFalse(ScreenMemoryStatus().isRecording)
        XCTAssertEqual(off.allowBrowsers, [])
    }

    func testAllowBrowsersIsParsedAndDefaultsToEmpty() {
        let withAllowed = ScreenMemoryStatus.parse([
            "enabled": true, "allow_browsers": ["com.apple.Safari", "com.brave.Browser"],
        ])
        XCTAssertEqual(withAllowed.allowBrowsers, ["com.apple.Safari", "com.brave.Browser"])
        XCTAssertTrue(withAllowed.allowBrowsers.contains("com.apple.Safari"))

        let missing = ScreenMemoryStatus.parse(["enabled": true])
        XCTAssertEqual(missing.allowBrowsers, [])
    }

    func testRecordingPausedAndStopped() {
        let obj: [String: Any] = ["enabled": true, "paused": false, "running": true,
                                  "captures": 12,
                                  "last": ["app": "Safari", "time": "2026-09-25 10:42"]]
        var status = ScreenMemoryStatus.parse(obj)
        XCTAssertTrue(status.isRecording)
        XCTAssertEqual(status.lastTime, "10:42")
        XCTAssertEqual(status.summary, "Remembering screen text · 12 saved · last: Safari 10:42")
        status.paused = true
        XCTAssertFalse(status.isRecording)
        XCTAssertEqual(status.summary, "Paused · 12 saved")
        status.paused = false
        status.running = false
        XCTAssertFalse(status.isRecording)
        XCTAssertTrue(status.summary.hasPrefix("Not running"))
    }
}

final class ConversationGrantTests: XCTestCase {
    func testConfirmRequestCarriesTheOfferedGrant() {
        let offered = SidecarEvent.parse(["type": "confirm_request", "request_id": "r1",
                                          "description": "open https://docs.example.com",
                                          "grant": "open pages on docs.example.com"],
                                         fallbackGoal: "")
        guard case let .confirmRequest(rid, _, grant)? = offered.first else {
            return XCTFail("expected a confirmation")
        }
        XCTAssertEqual(rid, "r1")
        XCTAssertEqual(grant, "open pages on docs.example.com")
        let plain = SidecarEvent.parse(["type": "confirm_request", "request_id": "r2",
                                        "description": "delete x"], fallbackGoal: "")
        guard case let .confirmRequest(_, _, none)? = plain.first else {
            return XCTFail("expected a confirmation")
        }
        XCTAssertEqual(none, "")
    }
}

final class AuditEntryTests: XCTestCase {
    func testParseAndHeadline() {
        let e = AuditEntry.parse(["id": "abc123def456", "ts": 1_790_000_000.0,
                                  "event": "confirmation", "tool": "run_shell",
                                  "confirmed": false, "summary": "make"])
        XCTAssertEqual(e?.headline, "confirmation · run_shell · declined")
        XCTAssertEqual(e?.symbol, "hand.raised")
        let start = AuditEntry.parse(["id": "x", "ts": 1.0, "event": "run_start"])
        XCTAssertEqual(start?.headline, "run start")
        XCTAssertNil(start?.confirmed)
        XCTAssertNil(AuditEntry.parse(["event": "action"]))
    }
}

final class TapSequenceTests: XCTestCase {
    func testDoubleAndTripleTaps() {
        var escape = TapSequence(count: 2, gap: 0.4)
        XCTAssertFalse(escape.register(at: 10.0))
        XCTAssertTrue(escape.register(at: 10.3))
        XCTAssertFalse(escape.register(at: 10.5))       // starts over after a hit
        XCTAssertFalse(escape.register(at: 11.2))       // too slow
        XCTAssertTrue(escape.register(at: 11.5))

        var control = TapSequence(count: 3, gap: 0.35)
        XCTAssertFalse(control.register(at: 1.0))
        XCTAssertFalse(control.register(at: 1.3))
        control.reset()                                 // another key in between
        XCTAssertFalse(control.register(at: 1.5))
        XCTAssertFalse(control.register(at: 1.7))
        XCTAssertTrue(control.register(at: 1.9))
        XCTAssertFalse(control.register(at: 0.5))       // clock went backwards: start over
    }
}

final class RedactionNoteTests: XCTestCase {
    func testRedactionEventsCountOnTheReply() {
        var t = ChatTranscript()
        t.send("read my screen")
        for event in SidecarEvent.parse(["type": "redaction", "step": 1, "tool": "get_screen_context",
                                         "count": 2, "total": 2], fallbackGoal: "") {
            t.apply(event)
        }
        t.apply(.step(["type": "redaction", "count": 1]))   // no total: adds
        XCTAssertEqual(t.messages.last?.redacted, 3)
    }
}

final class ScreenHintTests: XCTestCase {
    func testParse() {
        let hint = ScreenHint.parse(["hint": "Press ⌘⇧T to reopen the tab.", "reason": "You just closed one.",
                                     "category": "shortcut", "confidence": 0.9])
        XCTAssertEqual(hint?.symbol, "keyboard")
        XCTAssertEqual(hint?.kindName, "shortcut")
        XCTAssertEqual(ScreenHint.parse(["hint": "x", "reason": "y", "category": "next_step"])?.kindName,
                       "next-step")
        XCTAssertNil(ScreenHint.parse(["hint": "x", "category": "fix"]))   // no reason, not shown
    }
}

final class MeetingAudioTests: XCTestCase {
    func testPickApp() {
        XCTAssertEqual(MeetingAudio.pickApp(running: ["us.zoom.xos", "com.apple.Safari"],
                                            frontmost: "com.apple.Safari", own: "dev.aether.macos"),
                       "us.zoom.xos")
        XCTAssertEqual(MeetingAudio.pickApp(running: ["us.zoom.xos", "com.apple.FaceTime"],
                                            frontmost: "com.apple.FaceTime", own: nil),
                       "com.apple.FaceTime")
        XCTAssertEqual(MeetingAudio.pickApp(running: ["com.google.Chrome"],
                                            frontmost: "com.google.Chrome", own: nil),
                       "com.google.Chrome")
        XCTAssertNil(MeetingAudio.pickApp(running: [], frontmost: "dev.aether.macos",
                                          own: "dev.aether.macos"))
    }

    func testResampleSilenceAndWav() {
        let one = [Float](repeating: 0.5, count: 48_000)
        XCTAssertEqual(MeetingAudio.resample(one, from: 48_000, to: 16_000).count, 16_000)
        XCTAssertEqual(MeetingAudio.resample(one, from: 16_000, to: 16_000).count, 48_000)
        XCTAssertFalse(MeetingAudio.worthSending([Float](repeating: 0, count: 16_000)))
        XCTAssertFalse(MeetingAudio.worthSending([Float](repeating: 0.5, count: 100)))
        XCTAssertTrue(MeetingAudio.worthSending([Float](repeating: 0.1, count: 16_000)))
        let wav = MeetingAudio.wav([0, 0.5, -0.5, 1.5])
        XCTAssertEqual(wav.count, 44 + 8)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(String(data: wav[8 ..< 16], encoding: .ascii), "WAVEfmt ")
        XCTAssertEqual(String(data: wav[36 ..< 40], encoding: .ascii), "data")
        // clipped to full scale
        let last = wav[50 ..< 52].withUnsafeBytes { $0.loadUnaligned(as: Int16.self) }
        XCTAssertEqual(Int16(littleEndian: last), Int16.max)
    }

    func testTranscriptionConsentText() {
        let local = MeetingTranscription.parse(["engine": "local", "ready": true, "summarize": true])
        XCTAssertEqual(local.whereText, "on this Mac")
        XCTAssertEqual(MeetingTranscription.parse(["engine": "groq"]).whereText, "with Groq")
        XCTAssertFalse(MeetingTranscription.parse([:]).ready)
    }
}

/// A fake `MicInput` so `MicHub`'s subscriber bookkeeping can be tested without a
/// real `AVAudioEngine` (there is no microphone on the CI/Linux box this runs on).
private final class FakeMicInput: MicInput {
    var format: AVAudioFormat
    private(set) var isRunning = false
    private(set) var tapInstallCount = 0
    private(set) var tapRemoveCount = 0
    private(set) var startCount = 0
    private(set) var stopCount = 0
    var startError: Error?
    private var tapBlock: ((AVAudioPCMBuffer, AVAudioTime) -> Void)?

    init(format: AVAudioFormat = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!) {
        self.format = format
    }

    func installTap(_ block: @escaping (AVAudioPCMBuffer, AVAudioTime) -> Void) {
        tapInstallCount += 1
        tapBlock = block
    }

    func removeTap() {
        tapRemoveCount += 1
        tapBlock = nil
    }

    func start() throws {
        if let startError {
            throw startError
        }
        startCount += 1
        isRunning = true
    }

    func stop() {
        stopCount += 1
        isRunning = false
    }

    /// Simulate the engine handing a buffer to whichever tap is installed.
    func deliver(_ buffer: AVAudioPCMBuffer) {
        tapBlock?(buffer, AVAudioTime(hostTime: 0))
    }
}

private func makeTestBuffer(frames: AVAudioFrameCount = 10,
                            format: AVAudioFormat = AVAudioFormat(standardFormatWithSampleRate: 48_000,
                                                                  channels: 1)!) -> AVAudioPCMBuffer {
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
    buffer.frameLength = frames
    return buffer
}

final class MicHubTests: XCTestCase {
    func testFirstSubscriberStartsTheInput() throws {
        let fake = FakeMicInput()
        let hub = MicHub(input: fake)
        XCTAssertFalse(fake.isRunning)
        // Keep the subscription alive: letting it go cancels it (and stops the mic).
        let sub = try hub.subscribe { _ in }
        XCTAssertTrue(fake.isRunning)
        XCTAssertEqual(fake.startCount, 1)
        XCTAssertEqual(fake.tapInstallCount, 1)
        sub.cancel()
    }

    func testDroppingTheLastSubscriptionReleasesTheMic() throws {
        let fake = FakeMicInput()
        let hub = MicHub(input: fake)
        do {
            _ = try hub.subscribe { _ in }
        }
        XCTAssertFalse(fake.isRunning)
        XCTAssertEqual(fake.stopCount, 1)
    }

    func testBufferFansOutToEverySubscriber() throws {
        let fake = FakeMicInput()
        let hub = MicHub(input: fake)
        var count1 = 0
        var count2 = 0
        let sub1 = try hub.subscribe { _ in count1 += 1 }
        let sub2 = try hub.subscribe { _ in count2 += 1 }
        fake.deliver(makeTestBuffer())
        XCTAssertEqual(count1, 1)
        XCTAssertEqual(count2, 1)
        sub1.cancel()
        sub2.cancel()
    }

    func testInputStopsOnlyWhenTheLastSubscriptionIsCancelled() throws {
        let fake = FakeMicInput()
        let hub = MicHub(input: fake)
        let sub1 = try hub.subscribe { _ in }
        let sub2 = try hub.subscribe { _ in }
        sub1.cancel()
        XCTAssertTrue(fake.isRunning)
        XCTAssertEqual(fake.tapRemoveCount, 0, "one subscriber remains; the tap must stay up")
        sub2.cancel()
        XCTAssertFalse(fake.isRunning)
        XCTAssertEqual(fake.tapRemoveCount, 1)
        XCTAssertEqual(fake.stopCount, 1)
    }

    func testCancellingTwiceIsHarmless() throws {
        let fake = FakeMicInput()
        let hub = MicHub(input: fake)
        let sub = try hub.subscribe { _ in }
        sub.cancel()
        sub.cancel()
        XCTAssertEqual(fake.stopCount, 1)
        XCTAssertEqual(fake.tapRemoveCount, 1)
    }

    func testThrowingStartRollsBackTheSubscription() {
        let fake = FakeMicInput()
        fake.startError = NSError(domain: "test", code: 1)
        let hub = MicHub(input: fake)
        XCTAssertThrowsError(try hub.subscribe { _ in })
        XCTAssertEqual(fake.tapInstallCount, 1)
        XCTAssertEqual(fake.tapRemoveCount, 1, "the tap installed for the failed start must be removed")
        XCTAssertFalse(fake.isRunning)

        // A later, successful subscribe should still work — nothing was left dangling.
        fake.startError = nil
        var delivered = 0
        let sub = try? hub.subscribe { _ in delivered += 1 }
        XCTAssertNotNil(sub)
        XCTAssertTrue(fake.isRunning)
        fake.deliver(makeTestBuffer())
        XCTAssertEqual(delivered, 1)
        sub?.cancel()
    }
}

final class SpeculationPolicyTests: XCTestCase {
    func testFiresOnlyOnceStable() {
        var policy = SpeculationPolicy(stableSeconds: 1.0, minWords: 4, maxFires: 2)
        policy.observe("where is the wifi setting", at: 0.0)
        XCTAssertNil(policy.fire(at: 0.5), "should not fire before the stable window elapses")
        XCTAssertEqual(policy.fire(at: 1.0), "where is the wifi setting")
    }

    func testDoesNotFireBelowMinWords() {
        var policy = SpeculationPolicy(stableSeconds: 1.0, minWords: 4, maxFires: 2)
        policy.observe("turn it on", at: 0.0)
        XCTAssertNil(policy.fire(at: 5.0), "three words, below the four-word minimum")
    }

    func testFiresAtMostMaxFiresTimes() {
        var policy = SpeculationPolicy(stableSeconds: 1.0, minWords: 4, maxFires: 2)
        policy.observe("where is the wifi setting", at: 0.0)
        XCTAssertNotNil(policy.fire(at: 1.0))
        policy.observe("where is the wifi setting please", at: 1.1)
        XCTAssertNotNil(policy.fire(at: 2.2))
        policy.observe("where is the wifi setting please now", at: 2.3)
        XCTAssertNil(policy.fire(at: 3.4), "a third fire is past the budget")
    }

    func testDoesNotFireTwiceForTheSameText() {
        var policy = SpeculationPolicy(stableSeconds: 1.0, minWords: 4, maxFires: 2)
        policy.observe("where is the wifi setting", at: 0.0)
        XCTAssertNotNil(policy.fire(at: 1.0))
        // Same (normalized) text, still stable: nothing new to fire on.
        XCTAssertNil(policy.fire(at: 5.0))
    }

    func testStabilityResetsWhenThePartialChanges() {
        var policy = SpeculationPolicy(stableSeconds: 1.0, minWords: 4, maxFires: 2)
        policy.observe("where is the wifi setting", at: 0.0)
        XCTAssertNil(policy.fire(at: 0.9))
        policy.observe("where is the bluetooth setting", at: 0.95)  // changed just before firing
        XCTAssertNil(policy.fire(at: 1.5), "the clock restarted on the new text")
        XCTAssertEqual(policy.fire(at: 1.95), "where is the bluetooth setting")
    }

    func testNormalizeIgnoresCasePunctuationAndCollapsesWhitespace() {
        XCTAssertEqual(SpeculationPolicy.normalize("  Where  is   the WiFi?! "), "where is the wifi")
        XCTAssertTrue(SpeculationPolicy.matches("Where is the Wi-Fi setting?",
                                                "where is the wifi setting"))
        XCTAssertFalse(SpeculationPolicy.matches("where is the wifi setting",
                                                 "where is the bluetooth setting"))
    }
}

final class MicConverterTests: XCTestCase {
    private func fill(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        for c in 0 ..< Int(buffer.format.channelCount) {
            for i in 0 ..< Int(buffer.frameLength) {
                channels[c][i] = sinf(Float(i) * 0.05)
            }
        }
    }

    func testDownsamples48kMonoToRoughly16k() {
        let format = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 48_000)!
        buffer.frameLength = 48_000
        fill(buffer)

        let converter = MicConverter()
        // The resampler holds back its last few milliseconds until finish().
        let out = converter.convert(buffer) + converter.finish()
        XCTAssertTrue(abs(out.count - 16_000) <= 200, "expected ~16000 samples, got \(out.count)")
    }

    func testDownmixesAndDownsamples44_1kStereoToRoughly16k() {
        let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 2)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44_100)!
        buffer.frameLength = 44_100
        fill(buffer)

        let converter = MicConverter()
        // The resampler holds back its last few milliseconds until finish().
        let out = converter.convert(buffer) + converter.finish()
        XCTAssertTrue(abs(out.count - 16_000) <= 200, "expected ~16000 samples, got \(out.count)")
    }
}

final class PIMServiceJSONTests: XCTestCase {
    func testEventJSONEncodesEpochSecondsAndFields() {
        let start = Date(timeIntervalSince1970: 1_790_431_200)
        let end = Date(timeIntervalSince1970: 1_790_434_800)
        let json = PIMService.eventJSON(title: "Standup", start: start, end: end, allDay: false,
                                        location: "Zoom", calendar: "Work", notes: "sync",
                                        id: "abc")
        XCTAssertEqual(json["title"] as? String, "Standup")
        XCTAssertEqual(json["start"] as? Double, 1_790_431_200)
        XCTAssertEqual(json["end"] as? Double, 1_790_434_800)
        XCTAssertEqual(json["all_day"] as? Bool, false)
        XCTAssertEqual(json["location"] as? String, "Zoom")
        XCTAssertEqual(json["calendar"] as? String, "Work")
        XCTAssertEqual(json["notes"] as? String, "sync")
        XCTAssertEqual(json["id"] as? String, "abc")
    }

    func testEventJSONAllDayFlagAndOmitsEmptyOptionalFields() {
        let now = Date()
        let json = PIMService.eventJSON(title: "Holiday", start: now, end: now, allDay: true,
                                        location: "", calendar: nil, notes: "", id: nil)
        XCTAssertEqual(json["all_day"] as? Bool, true)
        XCTAssertNil(json["location"])
        XCTAssertNil(json["calendar"])
        XCTAssertNil(json["notes"])
        XCTAssertNil(json["id"])
    }

    func testEventJSONTruncatesNotesTo500Chars() {
        let now = Date()
        let longNotes = String(repeating: "x", count: 900)
        let json = PIMService.eventJSON(title: "T", start: now, end: now, allDay: false,
                                        location: nil, calendar: nil, notes: longNotes, id: nil)
        XCTAssertEqual((json["notes"] as? String)?.count, 500)
    }

    func testReminderJSONEncodesDueAndCompleted() {
        let due = Date(timeIntervalSince1970: 1_790_500_000)
        let json = PIMService.reminderJSON(title: "Buy milk", due: due, completed: true,
                                           list: "Errands", notes: "2%", id: "r1")
        XCTAssertEqual(json["due"] as? Double, 1_790_500_000)
        XCTAssertEqual(json["completed"] as? Bool, true)
        XCTAssertEqual(json["list"] as? String, "Errands")
        XCTAssertEqual(json["id"] as? String, "r1")
    }

    func testReminderJSONOmitsNilFields() {
        let json = PIMService.reminderJSON(title: "T", due: nil, completed: false, list: nil,
                                           notes: nil, id: nil)
        XCTAssertNil(json["due"])
        XCTAssertNil(json["list"])
        XCTAssertNil(json["notes"])
        XCTAssertEqual(json["completed"] as? Bool, false)
    }

    func testReminderJSONTruncatesNotesTo500Chars() {
        let longNotes = String(repeating: "y", count: 700)
        let json = PIMService.reminderJSON(title: "T", due: nil, completed: false, list: nil,
                                           notes: longNotes, id: nil)
        XCTAssertEqual((json["notes"] as? String)?.count, 500)
    }

    func testContactJSONOmitsEmptyOrganization() {
        let json = PIMService.contactJSON(name: "Sam Lee", organization: "",
                                          emails: ["sam@x.com"], phones: [])
        XCTAssertEqual(json["name"] as? String, "Sam Lee")
        XCTAssertNil(json["organization"])
        XCTAssertEqual(json["emails"] as? [String], ["sam@x.com"])
        XCTAssertEqual(json["phones"] as? [String], [])
    }

    func testContactJSONKeepsNonEmptyOrganization() {
        let json = PIMService.contactJSON(name: "Sam Lee", organization: "Acme",
                                          emails: [], phones: ["555-1234"])
        XCTAssertEqual(json["organization"] as? String, "Acme")
    }
}

final class IntegrationRowStateTests: XCTestCase {
    func testConnectedWhenEnabledAndNoDenial() {
        XCTAssertEqual(IntegrationRow.state(enabled: true, osStatus: "authorized"), .connected)
        // Notes/Mail have no EventKit status (nil) but can still be connected.
        XCTAssertEqual(IntegrationRow.state(enabled: true, osStatus: nil), .connected)
    }

    func testNotConnectedWhenDisabled() {
        XCTAssertEqual(IntegrationRow.state(enabled: false, osStatus: nil), .notConnected)
        XCTAssertEqual(IntegrationRow.state(enabled: false, osStatus: "not_determined"), .notConnected)
    }

    func testDeniedOverridesTheEnabledFlag() {
        XCTAssertEqual(IntegrationRow.state(enabled: true, osStatus: "denied"), .denied)
        XCTAssertEqual(IntegrationRow.state(enabled: false, osStatus: "restricted"), .denied)
    }
}

final class NextMeetingPolicyTests: XCTestCase {
    private static let utc = TimeZone(identifier: "UTC")!
    private static let enGB = Locale(identifier: "en_GB")

    /// 2026-01-15 at `hh:mm` UTC, exactly (so label()'s formatted time is predictable).
    private func utcDate(_ hh: Int, _ mm: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = Self.utc
        var comps = DateComponents()
        comps.year = 2026; comps.month = 1; comps.day = 15
        comps.hour = hh; comps.minute = mm
        return calendar.date(from: comps)!
    }

    // MARK: UpcomingMeeting.from

    func testFromNilJSONIsNil() {
        XCTAssertNil(UpcomingMeeting.from(nil))
    }

    func testFromAllDayIsNil() {
        let json = PIMService.eventJSON(title: "Offsite", start: utcDate(9, 0), end: utcDate(17, 0),
                                        allDay: true, location: nil, calendar: nil, notes: nil, id: nil)
        XCTAssertNil(UpcomingMeeting.from(json))
    }

    func testFromMissingOrEmptyTitleIsNil() {
        var json = PIMService.eventJSON(title: "", start: utcDate(9, 0), end: utcDate(10, 0),
                                        allDay: false, location: nil, calendar: nil, notes: nil, id: nil)
        XCTAssertNil(UpcomingMeeting.from(json))
        json.removeValue(forKey: "title")
        XCTAssertNil(UpcomingMeeting.from(json))
    }

    func testFromEndNotAfterStartIsNil() {
        let same = utcDate(9, 0)
        let json = PIMService.eventJSON(title: "Standup", start: same, end: same, allDay: false,
                                        location: nil, calendar: nil, notes: nil, id: nil)
        XCTAssertNil(UpcomingMeeting.from(json))
    }

    func testFromValidEventProducesMeeting() {
        let start = utcDate(14, 0)
        let end = utcDate(14, 30)
        let json = PIMService.eventJSON(title: "Budget sync", start: start, end: end, allDay: false,
                                        location: "Zoom", calendar: "Work", notes: nil, id: "e1")
        XCTAssertEqual(UpcomingMeeting.from(json), UpcomingMeeting(title: "Budget sync", start: start, end: end))
    }

    // MARK: shown

    func testShownWhileRunning() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(14, 0), end: utcDate(14, 30))
        XCTAssertEqual(NextMeetingPolicy.shown(meeting, now: utcDate(14, 10)), meeting)
    }

    func testShownStartingIn30Minutes() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(14, 30), end: utcDate(15, 0))
        XCTAssertEqual(NextMeetingPolicy.shown(meeting, now: utcDate(14, 0)), meeting)
    }

    func testNotShownStartingIn90Minutes() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(15, 30), end: utcDate(16, 0))
        XCTAssertNil(NextMeetingPolicy.shown(meeting, now: utcDate(14, 0)))
    }

    func testNotShownAlreadyEnded() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(13, 0), end: utcDate(13, 30))
        XCTAssertNil(NextMeetingPolicy.shown(meeting, now: utcDate(14, 0)))
    }

    // MARK: offersNotes

    func testOffersNotesFiveMinutesBefore() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(14, 5), end: utcDate(14, 30))
        XCTAssertTrue(NextMeetingPolicy.offersNotes(meeting, now: utcDate(14, 0)))
    }

    func testDoesNotOfferNotesFifteenMinutesBefore() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(14, 15), end: utcDate(14, 30))
        XCTAssertFalse(NextMeetingPolicy.offersNotes(meeting, now: utcDate(14, 0)))
    }

    func testOffersNotesWhileRunning() {
        let meeting = UpcomingMeeting(title: "Standup", start: utcDate(14, 0), end: utcDate(14, 30))
        XCTAssertTrue(NextMeetingPolicy.offersNotes(meeting, now: utcDate(14, 10)))
    }

    // MARK: label

    func testLabelRoundsUpMinutesUntilStart() {
        let start = utcDate(14, 0)
        let meeting = UpcomingMeeting(title: "Budget sync", start: start, end: utcDate(14, 30))
        let now = start.addingTimeInterval(-24.5 * 60)   // 24 min 30 s before
        XCTAssertEqual(NextMeetingPolicy.label(meeting, now: now, locale: Self.enGB, timeZone: Self.utc),
                       "Next: Budget sync · 14:00 (in 25 min)")
    }

    func testLabelShowsNowInTheLastMinute() {
        let start = utcDate(14, 0)
        let meeting = UpcomingMeeting(title: "Budget sync", start: start, end: utcDate(14, 30))
        let now = start.addingTimeInterval(-20)   // 20 s before
        XCTAssertEqual(NextMeetingPolicy.label(meeting, now: now, locale: Self.enGB, timeZone: Self.utc),
                       "Next: Budget sync · 14:00 (now)")
    }

    func testLabelWhileRunning() {
        let meeting = UpcomingMeeting(title: "Budget sync", start: utcDate(14, 0), end: utcDate(14, 30))
        let now = utcDate(14, 10)
        XCTAssertEqual(NextMeetingPolicy.label(meeting, now: now, locale: Self.enGB, timeZone: Self.utc),
                       "Now: Budget sync · until 14:30")
    }

    func testLabelRoundsUpASecondPastWholeMinutes() {
        let start = utcDate(14, 0)
        let meeting = UpcomingMeeting(title: "Budget sync", start: start, end: utcDate(14, 30))
        let now = start.addingTimeInterval(-(24 * 60 + 1))   // 24 min 1 s before
        XCTAssertEqual(NextMeetingPolicy.label(meeting, now: now, locale: Self.enGB, timeZone: Self.utc),
                       "Next: Budget sync · 14:00 (in 25 min)")
    }

    func testLabelTruncatesLongTitles() {
        let longTitle = String(repeating: "a", count: 50)
        let start = utcDate(14, 0)
        let meeting = UpcomingMeeting(title: longTitle, start: start, end: utcDate(14, 30))
        let now = start.addingTimeInterval(-5 * 60)
        let expectedTitle = String(longTitle.prefix(40)) + "…"
        XCTAssertEqual(NextMeetingPolicy.label(meeting, now: now, locale: Self.enGB, timeZone: Self.utc),
                       "Next: \(expectedTitle) · 14:00 (in 5 min)")
    }
}

final class NearestEventTests: XCTestCase {
    private struct Event {
        let name: String
        let start: Date
        let end: Date
        let allDay: Bool
    }

    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func pick(_ events: [Event]) -> String? {
        PIMService.nearestEvent(events, now: now, start: { $0.start }, end: { $0.end },
                                allDay: { $0.allDay })?.name
    }

    func testAMeetingAboutToStartWinsOverALongRunningBlock() {
        let events = [
            Event(name: "focus", start: now.addingTimeInterval(-3 * 3600),
                  end: now.addingTimeInterval(2 * 3600), allDay: false),
            Event(name: "sync", start: now.addingTimeInterval(5 * 60),
                  end: now.addingTimeInterval(35 * 60), allDay: false),
        ]
        XCTAssertEqual(pick(events), "sync")
    }

    func testAMeetingThatJustStartedWinsOverALaterOne() {
        let events = [
            Event(name: "later", start: now.addingTimeInterval(30 * 60),
                  end: now.addingTimeInterval(60 * 60), allDay: false),
            Event(name: "standup", start: now.addingTimeInterval(-2 * 60),
                  end: now.addingTimeInterval(13 * 60), allDay: false),
        ]
        XCTAssertEqual(pick(events), "standup")
    }

    func testAllDayAndEndedEventsAreSkipped() {
        let events = [
            Event(name: "holiday", start: now.addingTimeInterval(-60),
                  end: now.addingTimeInterval(86_000), allDay: true),
            Event(name: "done", start: now.addingTimeInterval(-3600),
                  end: now.addingTimeInterval(-60), allDay: false),
        ]
        XCTAssertNil(pick(events))
    }
}
