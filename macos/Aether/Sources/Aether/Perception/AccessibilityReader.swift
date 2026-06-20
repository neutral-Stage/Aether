import ApplicationServices
import AppKit
import Foundation

/// Native Accessibility tree reader (Phase 3 path — supplements sidecar tools).
enum AccessibilityReader {
    static func isTrusted(prompt: Bool = false) -> Bool {
        if prompt {
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            return AXIsProcessTrustedWithOptions(opts)
        }
        return AXIsProcessTrusted()
    }

    static func frontmostAppName() -> String {
        NSWorkspace.shared.frontmostApplication?.localizedName ?? "Unknown"
    }

    /// Compact summary of the focused app's AX tree (first N elements).
    static func screenContextSummary(maxElements: Int = 24) -> String {
        guard isTrusted() else {
            return "Accessibility not granted — enable in System Settings."
        }
        let appName = frontmostAppName()
        guard let app = NSWorkspace.shared.frontmostApplication else {
            return "Frontmost app: \(appName)\n(no AX app element)"
        }
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        var lines: [String] = ["Frontmost app: \(appName)"]
        var count = 0
        func walk(_ element: AXUIElement, depth: Int) {
            guard count < maxElements, depth < 4 else { return }
            var roleRef: CFTypeRef?
            var titleRef: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &roleRef)
            AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &titleRef)
            let role = roleRef as? String ?? "?"
            let title = (titleRef as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !title.isEmpty || role == "AXButton" || role == "AXTextField" {
                lines.append("  [\(count)] \(role): \(title)")
                count += 1
            }
            var childrenRef: CFTypeRef?
            if AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &childrenRef) == .success,
               let children = childrenRef as? [AXUIElement] {
                for child in children.prefix(12) {
                    walk(child, depth: depth + 1)
                }
            }
        }
        walk(axApp, depth: 0)
        lines.append("(\(count) elements sampled)")
        return lines.joined(separator: "\n")
    }
}
