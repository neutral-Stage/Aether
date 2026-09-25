import AppKit
import SwiftUI

/// Conversations on the left; the open one on the right, with each reply's steps.
struct ChatView: View {
    @ObservedObject var store: ChatStore

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { store.selected },
                                    set: { id in Task { await store.open(id) } })) {
                ForEach(store.sessions) { s in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.title).lineLimit(1)
                        Text(s.updatedAt, style: .relative)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .tag(Optional(s.id))
                    .contextMenu {
                        Button("Delete", role: .destructive) { store.delete(s.id) }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 220)
            .toolbar {
                Button { store.newChat() } label: { Label("New chat", systemImage: "square.and.pencil") }
                    .disabled(store.transcript.isWorking)
            }
        } detail: {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            if store.transcript.messages.isEmpty {
                                Text(store.loadError ?? "Ask Aether to do something on your Mac.")
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, alignment: .center)
                                    .padding(.top, 40)
                            }
                            ForEach(store.transcript.messages) { m in
                                ChatBubble(message: m, onRetry: { store.retry(m) })
                                    .id(m.id)
                            }
                        }
                        .padding(16)
                    }
                    .onChange(of: store.transcript) { _, t in
                        if let last = t.messages.last {
                            withAnimation(.easeOut(duration: 0.15)) {
                                proxy.scrollTo(last.id, anchor: .bottom)
                            }
                        }
                    }
                }
                Divider()
                ChatComposer(store: store)
            }
        }
    }
}

struct ChatComposer: View {
    @ObservedObject var store: ChatStore

    var body: some View {
        HStack(spacing: 8) {
            TextField("What should I do?", text: $store.draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1 ... 5)
                .onSubmit { store.send() }
            if store.transcript.isWorking {
                Button(role: .destructive) { store.stop() } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
                .keyboardShortcut(.escape, modifiers: [])
            } else {
                Button { store.send() } label: { Label("Send", systemImage: "arrow.up.circle.fill") }
                    .keyboardShortcut(.return, modifiers: [.command])
                    .disabled(store.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(12)
    }
}

struct ChatBubble: View {
    let message: ChatMessage
    var onRetry: () -> Void
    @State private var showSteps = false

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 60)
                Text(message.text)
                    .textSelection(.enabled)
                    .padding(10)
                    .background(Color.accentColor.opacity(0.18), in: RoundedRectangle(cornerRadius: 12))
            }
        case .note:
            Text(message.text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
        case .assistant:
            VStack(alignment: .leading, spacing: 6) {
                if !message.steps.isEmpty {
                    DisclosureGroup(isExpanded: Binding(get: { showSteps || message.status == .working },
                                                        set: { showSteps = $0 })) {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(message.steps) { StepRow(step: $0) }
                        }
                    } label: {
                        Text(stepsLabel).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if message.status == .working {
                    HStack(alignment: .top, spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(message.waitingOn.isEmpty
                             ? (message.narration.isEmpty ? "Working…" : message.narration)
                             : message.waitingOn)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Text(message.text.isEmpty ? "Done." : message.text)
                        .textSelection(.enabled)
                    if message.status != .done {
                        HStack {
                            Text(message.status == .stopped ? "Stopped" : "Didn't finish")
                                .font(.caption)
                                .foregroundStyle(.orange)
                            Button("Retry", action: onRetry).font(.caption)
                        }
                    }
                }
            }
            .padding(10)
            .frame(maxWidth: 560, alignment: .leading)
            .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
        }
    }

    private var stepsLabel: String {
        let n = message.steps.count
        let failed = message.steps.filter { $0.state == .failed }.count
        return "\(n) step\(n == 1 ? "" : "s")" + (failed > 0 ? " · \(failed) failed" : "")
    }
}

struct StepRow: View {
    let step: ChatStep

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                switch step.state {
                case .running: ProgressView().controlSize(.mini)
                case .done: Image(systemName: "checkmark.circle").foregroundStyle(.green)
                case .failed: Image(systemName: "xmark.circle").foregroundStyle(.red)
                }
                Text(step.description).font(.caption).lineLimit(2)
            }
            if !step.summary.isEmpty, step.state == .failed {
                Text(step.summary).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
            ForEach(step.screenshots, id: \.self) { path in
                if let image = NSImage(contentsOfFile: path) {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: 220, maxHeight: 140)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }
}
