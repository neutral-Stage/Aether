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
