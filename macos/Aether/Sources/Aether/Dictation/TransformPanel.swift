import AppKit
import SwiftUI

/// What the transform panel is doing.
@MainActor
final class TransformModel: ObservableObject {
    enum Phase: Equatable { case asking, working, preview, failed(String) }

    static let quick = ["Fix spelling and grammar", "Make it shorter", "Make it more formal",
                        "Make it friendlier", "Turn into bullet points", "Translate to English"]

    let original: String
    @Published var instruction = ""
    @Published var result = ""
    @Published var phase = Phase.asking

    init(original: String) {
        self.original = original
    }
}

/// ⌃⌥T: rewrite the selected text. Pick or type what to do, see the result next to the
/// original, then Replace (pasted into the app, so its own Undo works) or Cancel.
struct TransformView: View {
    @ObservedObject var model: TransformModel
    var run: (String) -> Void
    var apply: () -> Void
    var close: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Rewrite the selection", systemImage: "wand.and.stars")
                .font(.headline)
            Text(model.original)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(4)
                .frame(maxWidth: .infinity, alignment: .leading)
            switch model.phase {
            case .asking, .failed:
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150))], spacing: 6) {
                    ForEach(TransformModel.quick, id: \.self) { q in
                        Button(q) { run(q) }.buttonStyle(.bordered)
                    }
                }
                TextField("Or say what to do…", text: $model.instruction)
                    .textFieldStyle(.roundedBorder)
                    .focused($focused)
                    .onSubmit { run(model.instruction) }
                if case let .failed(message) = model.phase {
                    Text(message).font(.caption).foregroundStyle(.orange)
                }
            case .working:
                HStack { ProgressView().controlSize(.small); Text("Rewriting…") }
            case .preview:
                ScrollView {
                    Text(model.result)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
                .padding(8)
                .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
            HStack {
                Button("Cancel", action: close).keyboardShortcut(.cancelAction)
                Spacer()
                if model.phase == .preview {
                    Button("Try again") { model.phase = .asking }
                    Button("Replace", action: apply).keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(18)
        .frame(width: 460)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onAppear { focused = true }
    }
}

@MainActor
final class TransformPanel {
    private var panel: NSPanel?
    private var model: TransformModel?
    private var sourceApp: NSRunningApplication?
    private var bundleId = ""

    /// Read the selection in the app in front and ask what to do with it (or, with an
    /// instruction from a suggestion chip, start rewriting straight away).
    func begin(client: OrchestratorClient, instruction: String? = nil,
               onStatus: @escaping (String) -> Void) async {
        guard panel == nil else { return }
        let front = TextInsertion.frontmostApp()
        guard !TextInsertion.secureInputActive() else {
            onStatus("Rewriting is off in password fields.")
            return
        }
        let selected = await TextInsertion.selectedText()
        guard !selected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            onStatus("Select some text first, then press ⌃⌥T.")
            return
        }
        sourceApp = front.app
        bundleId = front.bundleId
        let model = TransformModel(original: selected)
        self.model = model
        let view = TransformView(
            model: model,
            run: { [weak self] instruction in self?.run(instruction, client: client) },
            apply: { [weak self] in self?.apply() },
            close: { [weak self] in self?.hide() })
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 480, height: 320),
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
        if let screen = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: screen.midX - 240, y: screen.midY - 160))
        }
        panel = p
        p.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let instruction, !instruction.isEmpty {
            run(instruction, client: client)
        }
    }

    private func run(_ instruction: String, client: OrchestratorClient) {
        guard let model else { return }
        let text = instruction.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        model.instruction = text
        model.phase = .working
        Task {
            do {
                model.result = try await client.transformText(model.original, instruction: text,
                                                              bundleId: bundleId)
                model.phase = .preview
            } catch {
                model.phase = .failed(error.localizedDescription)
            }
        }
    }

    private func apply() {
        guard let model, model.phase == .preview else { return }
        let text = model.result
        let app = sourceApp
        hide()
        Task {
            if let app {
                _ = app.activate()
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            guard !TextInsertion.secureInputActive() else { return }
            await TextInsertion.paste(text)     // replaces the selection; the app can undo it
        }
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
        model = nil
    }
}
