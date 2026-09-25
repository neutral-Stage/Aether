import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Putting text into the app in front, the way a person would: paste, then put the
/// clipboard back unless something else changed it meanwhile (yoclicky).
@MainActor
enum TextInsertion {
    /// A password field (or anything else that turned on Secure Event Input) has focus.
    static func secureInputActive() -> Bool {
        if IsSecureEventInputEnabled() { return true }
        guard let element = focusedElement() else { return false }
        let role = attribute(element, kAXRoleAttribute as String) as? String ?? ""
        let subrole = attribute(element, kAXSubroleAttribute as String) as? String ?? ""
        return role == "AXSecureTextField" || subrole == "AXSecureTextField"
    }

    static func frontmostApp() -> (name: String, bundleId: String, app: NSRunningApplication?) {
        let app = NSWorkspace.shared.frontmostApplication
        return (app?.localizedName ?? "", app?.bundleIdentifier ?? "", app)
    }

    /// The selected text in the focused element, from the accessibility API; falls back
    /// to ⌘C (clipboard restored afterwards) for apps that don't expose it.
    static func selectedText() async -> String {
        if let element = focusedElement(),
           let text = attribute(element, kAXSelectedTextAttribute as String) as? String,
           !text.isEmpty {
            return text
        }
        let pasteboard = NSPasteboard.general
        let saved = snapshot(pasteboard)
        let before = pasteboard.changeCount
        pressCommand(key: CGKeyCode(kVK_ANSI_C))
        try? await Task.sleep(nanoseconds: 200_000_000)
        guard pasteboard.changeCount != before else { return "" }
        let text = pasteboard.string(forType: .string) ?? ""
        restore(saved, to: pasteboard)
        return text
    }

    /// Paste `text` into the focused field, then restore the clipboard if untouched.
    static func paste(_ text: String) async {
        let pasteboard = NSPasteboard.general
        let saved = snapshot(pasteboard)
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        let ours = pasteboard.changeCount
        pressCommand(key: CGKeyCode(kVK_ANSI_V))
        try? await Task.sleep(nanoseconds: 350_000_000)
        if pasteboard.changeCount == ours {
            restore(saved, to: pasteboard)
        }
    }

    // MARK: helpers

    private typealias Snapshot = [[NSPasteboard.PasteboardType: Data]]

    private static func snapshot(_ pasteboard: NSPasteboard) -> Snapshot {
        (pasteboard.pasteboardItems ?? []).map { item in
            var entry: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types {
                if let data = item.data(forType: type) { entry[type] = data }
            }
            return entry
        }
    }

    private static func restore(_ saved: Snapshot, to pasteboard: NSPasteboard) {
        pasteboard.clearContents()
        guard !saved.isEmpty else { return }
        let items = saved.map { entry -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (type, data) in entry { item.setData(data, forType: type) }
            return item
        }
        pasteboard.writeObjects(items)
    }

    private static func pressCommand(key: CGKeyCode) {
        let source = CGEventSource(stateID: .combinedSessionState)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false)
        else { return }
        down.flags = .maskCommand
        up.flags = .maskCommand
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }

    private static func focusedElement() -> AXUIElement? {
        let system = AXUIElementCreateSystemWide()
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString,
                                            &value) == .success, let value else { return nil }
        return (value as! AXUIElement)  // swiftlint:disable:this force_cast
    }

    private static func attribute(_ element: AXUIElement, _ name: String) -> Any? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
            return nil
        }
        return value
    }
}
