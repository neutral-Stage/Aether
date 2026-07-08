import Foundation
import Security

/// Keychain-backed storage for provider API keys and the sidecar auth token
/// (Phase 7). Keys never touch disk in plaintext; the supervisor injects them
/// into the sidecar subprocess environment, so the Python side needs no changes.
enum KeyStore {
    static let keyService = "com.aether.keys"
    static let tokenService = "com.aether.token"
    static let tokenAccount = "sidecar-token"

    /// Provider env-var names Aether understands (mirror configs/router.yaml).
    static let providerAccounts = [
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY",
    ]

    // MARK: generic Keychain access

    private static func read(service: String, account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8), !value.isEmpty
        else { return nil }
        return value
    }

    @discardableResult
    private static func write(service: String, account: String, value: String) -> Bool {
        delete(service: service, account: account)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    @discardableResult
    private static func delete(service: String, account: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    // MARK: provider keys

    static func providerKey(_ name: String) -> String? {
        read(service: keyService, account: name)
    }

    static func hasProviderKey(_ name: String) -> Bool {
        providerKey(name) != nil
    }

    @discardableResult
    static func setProviderKey(_ name: String, _ value: String) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty
            ? delete(service: keyService, account: name)
            : write(service: keyService, account: name, value: trimmed)
    }

    // MARK: sidecar auth token (auth-on-by-default)

    static func sidecarToken() -> String? {
        read(service: tokenService, account: tokenAccount)
    }

    /// Return the token, generating a random one on first run.
    @discardableResult
    static func ensureSidecarToken() -> String {
        if let existing = sidecarToken() { return existing }
        var bytes = [UInt8](repeating: 0, count: 24)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let token = bytes.map { String(format: "%02x", $0) }.joined()
        _ = write(service: tokenService, account: tokenAccount, value: token)
        return token
    }

    /// Environment to inject into the sidecar subprocess: auth token + any keys.
    static func sidecarEnv() -> [String: String] {
        var env: [String: String] = ["AETHER_SIDECAR_TOKEN": ensureSidecarToken()]
        for name in providerAccounts {
            if let value = providerKey(name) { env[name] = value }
        }
        return env
    }
}
