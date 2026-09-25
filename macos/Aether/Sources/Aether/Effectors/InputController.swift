import AppKit
import CoreGraphics
import Foundation

/// Native CGEvent input (Phase 3 proof path — sidecar still owns default tool execution).
enum InputController {
    static func click(at point: CGPoint, button: CGMouseButton = .left) {
        let down: CGEventType = button == .right ? .rightMouseDown : .leftMouseDown
        let up: CGEventType = button == .right ? .rightMouseUp : .leftMouseUp
        let mouseButton: CGMouseButton = button == .right ? .right : .left

        if let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: mouseButton) {
            move.post(tap: .cghidEventTap)
        }
        if let d = CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: point, mouseButton: mouseButton) {
            d.post(tap: .cghidEventTap)
        }
        if let u = CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: point, mouseButton: mouseButton) {
            u.post(tap: .cghidEventTap)
        }
    }

    /// UTF-16 code units per user-perceived character. CGEvent's unicode string
    /// is UTF-16, so an emoji (a surrogate pair) must be sent as two units in one
    /// event; the old `UniChar(scalar.value)` trapped on scalars above U+FFFF.
    static func utf16Chunks(_ text: String) -> [[UniChar]] {
        text.map { Array(String($0).utf16) }
    }

    static func typeText(_ text: String) {
        for var units in utf16Chunks(text) {
            guard !units.isEmpty,
                  let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
            else { continue }
            down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
            up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
        }
    }
}
