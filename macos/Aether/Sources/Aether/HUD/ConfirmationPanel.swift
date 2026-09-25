import AppKit
import SwiftUI

struct ConfirmationView: View {
    let description: String
    /// What "allow for this conversation" covers; empty when it isn't offered.
    var grant = ""
    /// true: also allow `grant` for the rest of the conversation.
    let onApprove: (Bool) -> Void
    let onDecline: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Confirm action", systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
                .foregroundStyle(.orange)
            Text(description)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
            Text("Say “yes” / “no” or use the buttons below.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("No", role: .cancel, action: onDecline)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Yes", role: .destructive) { onApprove(false) }
                    .keyboardShortcut(.defaultAction)
            }
            if !grant.isEmpty {
                Button {
                    onApprove(true)
                } label: {
                    Text("Yes, and don't ask again in this conversation to \(grant)")
                        .font(.caption)
                        .multilineTextAlignment(.leading)
                }
                .buttonStyle(.link)
                .help("Only for this conversation, and only for actions asked about because "
                      + "Aether read untrusted content. Revoke from the chat window.")
            }
        }
        .padding(18)
        .frame(width: 360)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.orange.opacity(0.4), lineWidth: 1)
        )
    }
}

/// One editable field of an outgoing message (to, subject, body, …).
struct DraftField: Identifiable, Equatable {
    var id: String { key }
    let key: String
    var value: String
    let long: Bool

    static func parse(_ o: [String: Any]) -> DraftField? {
        guard let key = o["key"] as? String, let value = o["value"] as? String else { return nil }
        return DraftField(key: key, value: value, long: o["long"] as? Bool ?? false)
    }

    var label: String { key.prefix(1).uppercased() + key.dropFirst() }
}

/// Approve an outgoing message after editing it (VoiceOS-style drafts).
struct DraftConfirmationView: View {
    let description: String
    @State var fields: [DraftField]
    let onSend: ([String: String]) -> Void
    let onDecline: () -> Void
    private let original: [String: String]

    init(description: String, fields: [DraftField],
         onSend: @escaping ([String: String]) -> Void, onDecline: @escaping () -> Void) {
        self.description = description
        _fields = State(initialValue: fields)
        self.onSend = onSend
        self.onDecline = onDecline
        original = Dictionary(uniqueKeysWithValues: fields.map { ($0.key, $0.value) })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Check before it goes out", systemImage: "paperplane.fill")
                .font(.headline)
                .foregroundStyle(.orange)
            Text(description.components(separatedBy: "\n").first ?? description)
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach($fields) { $field in
                VStack(alignment: .leading, spacing: 2) {
                    Text(field.label).font(.caption.weight(.semibold))
                    if field.long {
                        TextEditor(text: $field.value)
                            .font(.body)
                            .frame(minHeight: 90, maxHeight: 220)
                            .overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
                    } else {
                        TextField(field.label, text: $field.value)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }
            HStack {
                Button("Don't send", role: .cancel, action: onDecline)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Send") {
                    var edits: [String: String] = [:]
                    for f in fields where f.value != original[f.key] { edits[f.key] = f.value }
                    onSend(edits)
                }
                .keyboardShortcut(.return, modifiers: [.command])
            }
        }
        .padding(18)
        .frame(width: 460)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

@MainActor
final class ConfirmationPanel: NSObject {
    private var panel: NSPanel?
    private var hosting: NSHostingView<ConfirmationView>?

    func show(
        description: String,
        grant: String = "",
        onApprove: @escaping (Bool) -> Void,
        onDecline: @escaping () -> Void
    ) {
        hide()
        let view = ConfirmationView(
            description: description,
            grant: grant,
            onApprove: { [weak self] remember in
                onApprove(remember)
                self?.hide()
            },
            onDecline: { [weak self] in
                onDecline()
                self?.hide()
            }
        )
        let host = NSHostingView(rootView: view)
        hosting = host
        let p = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: grant.isEmpty ? 180 : 220),
            styleMask: [.nonactivatingPanel, .hudWindow],
            backing: .buffered,
            defer: false
        )
        p.isFloatingPanel = true
        p.level = .floating + 1
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.title = "Aether Confirm"
        p.contentView = host
        p.backgroundColor = .clear
        p.isOpaque = false
        position(panel: p)
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// An outgoing message: editable fields, then Send (with the edits) or Don't send.
    func showDraft(description: String, fields: [DraftField],
                   onSend: @escaping ([String: String]) -> Void,
                   onDecline: @escaping () -> Void) {
        hide()
        let view = DraftConfirmationView(
            description: description, fields: fields,
            onSend: { [weak self] edits in
                onSend(edits)
                self?.hide()
            },
            onDecline: { [weak self] in
                onDecline()
                self?.hide()
            })
        // Titled so it can become key and take typing.
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 480, height: 420),
                        styleMask: [.titled, .fullSizeContentView, .utilityWindow, .hudWindow],
                        backing: .buffered, defer: false)
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isFloatingPanel = true
        p.level = .floating + 1
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.contentView = NSHostingView(rootView: view)
        p.backgroundColor = .clear
        p.isOpaque = false
        position(panel: p)
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
        hosting = nil
    }

    private func position(panel: NSPanel) {
        guard let screen = NSScreen.main?.visibleFrame else { return }
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(
            x: screen.midX - size.width / 2,
            y: screen.midY - size.height / 2
        ))
    }
}
