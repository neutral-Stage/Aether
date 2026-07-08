import SwiftUI

/// In-app API-key entry (Phase 7). Keys go to the Keychain via `KeyStore`; the
/// sidecar supervisor injects them into the sidecar on next start. No `.env`.
struct KeysView: View {
    var onSaved: () -> Void = {}

    private let providers: [(label: String, account: String)] = [
        ("Anthropic (Claude)", "ANTHROPIC_API_KEY"),
        ("OpenAI", "OPENAI_API_KEY"),
        ("Groq", "GROQ_API_KEY"),
        ("Google (Gemini)", "GOOGLE_API_KEY"),
        ("OpenRouter", "OPENROUTER_API_KEY"),
        ("Z.ai (GLM)", "ZAI_API_KEY"),
    ]

    @State private var drafts: [String: String] = [:]
    @State private var saved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("API Keys")
                .font(.caption.weight(.semibold))
            Text("Stored in your Keychain and passed to the sidecar on restart.")
                .font(.caption2)
                .foregroundStyle(.secondary)

            ForEach(providers, id: \.account) { provider in
                HStack(spacing: 8) {
                    Text(provider.label)
                        .font(.caption)
                        .frame(width: 150, alignment: .leading)
                    SecureField(
                        KeyStore.hasProviderKey(provider.account) ? "•••• saved" : "paste key",
                        text: Binding(
                            get: { drafts[provider.account] ?? "" },
                            set: { drafts[provider.account] = $0 }
                        )
                    )
                    .textFieldStyle(.roundedBorder)
                    .font(.caption)
                    if KeyStore.hasProviderKey(provider.account) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                }
            }

            HStack {
                Button("Save & restart sidecar") { save() }
                if saved {
                    Text("Saved").font(.caption2).foregroundStyle(.green)
                }
            }
        }
    }

    private func save() {
        for (_, account) in providers {
            if let value = drafts[account], !value.trimmingCharacters(in: .whitespaces).isEmpty {
                KeyStore.setProviderKey(account, value)
            }
        }
        drafts = [:]
        saved = true
        onSaved()
    }
}
