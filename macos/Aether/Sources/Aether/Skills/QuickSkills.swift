import AppKit
import SwiftUI

/// A prompt, what it works on, where the answer goes, and an optional ⌃⌥-digit hotkey.
struct QuickSkill: Identifiable, Equatable {
    var id: String
    var name: String
    var prompt: String
    var capture: String          // selection | clipboard | screen_text | screenshot | spoken | none
    var destination: String      // paste | clipboard | speak | show | file
    var hotkey: String           // "1"-"9" or ""
    var filePath: String

    static let captures = ["selection", "clipboard", "screen_text", "screenshot", "spoken", "none"]
    static let destinations = ["paste", "clipboard", "speak", "show", "file"]

    static func blank() -> QuickSkill {
        QuickSkill(id: "", name: "", prompt: "", capture: "selection", destination: "show",
                   hotkey: "", filePath: "")
    }

    static func parse(_ o: [String: Any]) -> QuickSkill? {
        guard let id = o["id"] as? String, let name = o["name"] as? String else { return nil }
        return QuickSkill(id: id, name: name, prompt: o["prompt"] as? String ?? "",
                          capture: o["capture"] as? String ?? "selection",
                          destination: o["destination"] as? String ?? "show",
                          hotkey: o["hotkey"] as? String ?? "",
                          filePath: o["file_path"] as? String ?? "")
    }

    var json: [String: Any] {
        ["id": id, "name": name, "prompt": prompt, "capture": capture,
         "destination": destination, "hotkey": hotkey, "file_path": filePath]
    }

    /// kVK_ANSI_1 … kVK_ANSI_9.
    static func keyCode(for digit: String) -> UInt16? {
        ["1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25][digit]
    }
}

/// Runs quick skills from their hotkeys or the menu, and delivers the result.
@MainActor
final class QuickSkillsController: ObservableObject {
    @Published private(set) var skills: [QuickSkill] = []
    @Published private(set) var recordingSkillId: String?

    var onStatus: ((String) -> Void)?
    var speak: ((String) async -> Void)?

    private let client: OrchestratorClient
    private let audio: AudioEngine
    private var hotkeys: [CommandBarHotkeyController] = []
    private let resultPanel = SkillResultPanel()

    init(client: OrchestratorClient, audio: AudioEngine) {
        self.client = client
        self.audio = audio
    }

    func reload() async {
        skills = await client.listQuickSkills()
        hotkeys.forEach { $0.stop() }
        hotkeys = skills.compactMap { skill in
            guard let code = QuickSkill.keyCode(for: skill.hotkey) else { return nil }
            let hk = CommandBarHotkeyController(modifiers: [.control, .option], keyCode: code)
            let id = skill.id
            hk.onToggle = { [weak self] in Task { @MainActor in await self?.trigger(id) } }
            hk.start()
            return hk
        }
    }

    func trigger(_ id: String) async {
        guard let skill = skills.first(where: { $0.id == id }) else { return }
        if skill.capture == "spoken" {
            await toggleSpoken(skill)
            return
        }
        var selection = "", clipboard = ""
        switch skill.capture {
        case "selection":
            guard !TextInsertion.secureInputActive() else {
                onStatus?("Not reading a password field.")
                return
            }
            selection = await TextInsertion.selectedText()
        case "clipboard":
            clipboard = NSPasteboard.general.string(forType: .string) ?? ""
        default:
            break
        }
        await run(skill, selection: selection, clipboard: clipboard)
    }

    private func toggleSpoken(_ skill: QuickSkill) async {
        if recordingSkillId == skill.id {
            recordingSkillId = nil
            let wav = audio.stopRecording()
            onStatus?("Listening done…")
            let words = (try? await client.transcribe(wavData: wav, useVocabulary: true)) ?? ""
            await run(skill, spoken: words)
        } else if recordingSkillId == nil {
            do {
                try audio.startRecording()
                recordingSkillId = skill.id
                onStatus?("\(skill.name): speak, then press its hotkey again")
            } catch {
                onStatus?(error.localizedDescription)
            }
        }
    }

    private func run(_ skill: QuickSkill, selection: String = "", clipboard: String = "",
                     spoken: String = "") async {
        let target = TextInsertion.frontmostApp().app
        onStatus?("\(skill.name)…")
        do {
            let result = try await client.runQuickSkill(skill.id, selection: selection,
                                                        clipboard: clipboard, spoken: spoken)
            switch result.destination {
            case "paste":
                if let target, target != NSWorkspace.shared.frontmostApplication {
                    _ = target.activate()
                    try? await Task.sleep(nanoseconds: 150_000_000)
                }
                guard !TextInsertion.secureInputActive() else {
                    onStatus?("Not typing into a password field.")
                    return
                }
                await TextInsertion.paste(result.text)
                onStatus?("")
            case "clipboard":
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(result.text, forType: .string)
                onStatus?("\(skill.name): copied to the clipboard")
            case "speak":
                onStatus?("")
                await speak?(result.text)
            case "file":
                onStatus?("\(skill.name): added to \(result.file ?? "the file")")
            default:
                onStatus?("")
                resultPanel.show(title: skill.name, text: result.text)
            }
        } catch {
            onStatus?(error.localizedDescription)
        }
    }
}

/// A small panel showing a skill's answer, with Copy.
@MainActor
final class SkillResultPanel {
    private var panel: NSPanel?

    func show(title: String, text: String) {
        panel?.orderOut(nil)
        let view = VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            ScrollView {
                Text(text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 260)
            HStack {
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                }
                Button("Close") { [weak self] in self?.hide() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(16)
        .frame(width: 420)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 440, height: 320),
                        styleMask: [.titled, .fullSizeContentView, .utilityWindow, .hudWindow],
                        backing: .buffered, defer: false)
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isFloatingPanel = true
        p.level = .floating + 1
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.contentView = NSHostingView(rootView: view)
        if let screen = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: screen.maxX - 460, y: screen.maxY - 360))
        }
        panel = p
        p.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
    }
}

/// Settings: list, add, edit and delete quick skills.
struct QuickSkillsView: View {
    @ObservedObject var controller: QuickSkillsController
    let client: OrchestratorClient
    @State private var editing: QuickSkill?
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(controller.skills) { skill in
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(skill.name).font(.caption.weight(.semibold))
                        Text("\(skill.capture) → \(skill.destination)"
                             + (skill.hotkey.isEmpty ? "" : " · ⌃⌥\(skill.hotkey)"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Run") { Task { await controller.trigger(skill.id) } }.font(.caption)
                    Button("Edit") { editing = skill }.font(.caption)
                    Button(role: .destructive) {
                        Task { await client.deleteQuickSkill(skill.id); await controller.reload() }
                    } label: { Image(systemName: "trash") }
                    .font(.caption)
                }
            }
            Button("New skill") { editing = .blank() }.font(.caption)
            if !error.isEmpty { Text(error).font(.caption2).foregroundStyle(.orange) }
            if let binding = Binding($editing) {
                QuickSkillEditor(skill: binding, onSave: save, onCancel: { editing = nil })
            }
        }
        .task { await controller.reload() }
    }

    private func save(_ skill: QuickSkill) {
        Task {
            do {
                try await client.saveQuickSkill(skill)
                editing = nil
                error = ""
                await controller.reload()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

struct QuickSkillEditor: View {
    @Binding var skill: QuickSkill
    var onSave: (QuickSkill) -> Void
    var onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TextField("Name", text: $skill.name).textFieldStyle(.roundedBorder)
            Text("What should it do?").font(.caption2).foregroundStyle(.secondary)
            TextEditor(text: $skill.prompt)
                .font(.caption)
                .frame(minHeight: 60)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
            Picker("Works on", selection: $skill.capture) {
                ForEach(QuickSkill.captures, id: \.self) { Text($0).tag($0) }
            }
            Picker("Result goes to", selection: $skill.destination) {
                ForEach(QuickSkill.destinations, id: \.self) { Text($0).tag($0) }
            }
            if skill.destination == "file" {
                TextField("File, e.g. ~/Documents/notes.md", text: $skill.filePath)
                    .textFieldStyle(.roundedBorder)
            }
            Picker("Hotkey", selection: $skill.hotkey) {
                Text("None").tag("")
                ForEach((1 ... 9).map(String.init), id: \.self) { Text("⌃⌥\($0)").tag($0) }
            }
            HStack {
                Button("Cancel", action: onCancel)
                Spacer()
                Button("Save") { onSave(skill) }
                    .disabled(skill.name.trimmingCharacters(in: .whitespaces).isEmpty
                              || skill.prompt.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .font(.caption)
        .padding(8)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}
