import AppKit
import SwiftUI

/// The agent's ask_user question: option buttons, a free-text answer, or skip.
struct QuestionView: View {
    let question: String
    let options: [String]
    let onAnswer: (String?) -> Void

    @State private var text = ""
    @FocusState private var fieldFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Aether has a question", systemImage: "questionmark.bubble.fill")
                .font(.headline)
                .foregroundStyle(.blue)
            Text(question)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if !options.isEmpty {
                FlowButtons(options: options) { onAnswer($0) }
            }
            TextField("Type an answer, or hold the talk key and speak", text: $text)
                .textFieldStyle(.roundedBorder)
                .focused($fieldFocused)
                .onSubmit(submit)
            HStack {
                Button("Skip") { onAnswer(nil) }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Answer", action: submit)
                    .keyboardShortcut(.defaultAction)
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(18)
        .frame(width: 400)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.blue.opacity(0.35), lineWidth: 1)
        )
        .onAppear { fieldFocused = options.isEmpty }
    }

    private func submit() {
        let answer = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !answer.isEmpty else { return }
        onAnswer(answer)
    }
}

/// Option buttons laid out in rows of up to three.
private struct FlowButtons: View {
    let options: [String]
    let onPick: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(stride(from: 0, to: options.count, by: 3)), id: \.self) { start in
                HStack(spacing: 6) {
                    ForEach(options[start ..< min(start + 3, options.count)], id: \.self) { option in
                        Button(option) { onPick(option) }
                            .buttonStyle(.bordered)
                    }
                }
            }
        }
    }
}

@MainActor
final class QuestionPanel: NSObject {
    private var panel: NSPanel?

    var isVisible: Bool { panel != nil }

    func show(question: String, options: [String], onAnswer: @escaping (String?) -> Void) {
        hide()
        let view = QuestionView(question: question, options: options) { [weak self] answer in
            onAnswer(answer)
            self?.hide()
        }
        let host = NSHostingView(rootView: view)
        // Titled (not borderless) so the panel can become key and take typing.
        let p = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 220),
            styleMask: [.titled, .fullSizeContentView, .utilityWindow, .hudWindow],
            backing: .buffered,
            defer: false
        )
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isFloatingPanel = true
        p.level = .floating + 1
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.contentView = host
        p.backgroundColor = .clear
        p.isOpaque = false
        if let screen = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: screen.midX - 210, y: screen.midY - 110))
        }
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
    }
}
