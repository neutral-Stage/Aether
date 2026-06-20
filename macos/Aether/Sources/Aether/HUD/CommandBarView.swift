import SwiftUI

struct CommandBarView: View {
    @Binding var goalText: String
    var isRunning: Bool
    var onSubmit: (String) -> Void
    var onDismiss: () -> Void

    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .foregroundStyle(.secondary)
            TextField("What should Aether do?", text: $goalText)
                .textFieldStyle(.plain)
                .font(.title3)
                .focused($focused)
                .onSubmit { submit() }
            if isRunning {
                ProgressView().controlSize(.small)
            }
            Button(action: submit) {
                Image(systemName: "return")
            }
            .buttonStyle(.borderless)
            .disabled(goalText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isRunning)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(width: 520)
        .background(.ultraThickMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(.white.opacity(0.2), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.25), radius: 20, y: 8)
        .onAppear { focused = true }
        .onExitCommand { onDismiss() }
    }

    private func submit() {
        let trimmed = goalText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        onSubmit(trimmed)
        goalText = ""
        onDismiss()
    }
}
