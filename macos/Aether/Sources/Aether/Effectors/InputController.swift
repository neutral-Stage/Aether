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

    static func typeText(_ text: String) {
        text.unicodeScalars.forEach { scalar in
            var uni = UniChar(scalar.value)
            if let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
               let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) {
                down.keyboardSetUnicodeString(stringLength: 1, unicodeString: &uni)
                up.keyboardSetUnicodeString(stringLength: 1, unicodeString: &uni)
                down.post(tap: .cghidEventTap)
                up.post(tap: .cghidEventTap)
            }
        }
    }
}
