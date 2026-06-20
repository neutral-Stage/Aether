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
