import ApplicationServices
import AppKit

/// Native AXPress action (delegates to AX when element index maps to live tree).
enum AXActions {
    static func pressFocusedElement() -> Bool {
        guard AccessibilityReader.isTrusted() else { return false }
        guard let app = NSWorkspace.shared.frontmostApplication else { return false }
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        var focusedRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(axApp, kAXFocusedUIElementAttribute as CFString, &focusedRef) == .success,
              let focused = focusedRef else { return false }
        let element = focused as! AXUIElement
        let err = AXUIElementPerformAction(element, kAXPressAction as CFString)
        return err == .success
    }
}
