import Foundation
import Security

/// macOS Keychain bridge for the audit log HMAC key (§NFR-5, Phase 12+).
///
/// Service `com.aether.audit` is read by the Python sidecar via the `security` CLI.
enum AuditKeychain {
    static let service = "com.aether.audit"
    static let account = "hmac-key"

    /// Legacy on-disk key written by Python before Keychain migration.
    static var legacyKeyFileURL: URL? {
        if let root = ProcessInfo.processInfo.environment["AETHER_ROOT"]?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !root.isEmpty {
            let url = URL(fileURLWithPath: root).appendingPathComponent("data/.audit_hmac_key")
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        let cwd = FileManager.default.currentDirectoryPath
        let candidate = URL(fileURLWithPath: cwd).appendingPathComponent("data/.audit_hmac_key")
        if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        return nil
    }

    static func loadKey() -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data, !data.isEmpty else {
            return nil
        }
        return data
    }

    @discardableResult
    static func saveKey(_ key: Data) -> Bool {
        deleteKey()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: key,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    @discardableResult
    static func deleteKey() -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    /// Ensure a key exists in Keychain, migrating from the legacy file when present.
    @discardableResult
    static func ensureKey(migratingFromFile fileURL: URL? = legacyKeyFileURL) -> Data {
        if let existing = loadKey() { return existing }
        if let fileURL,
           let fileData = try? Data(contentsOf: fileURL),
           !fileData.isEmpty,
           saveKey(fileData) {
            try? FileManager.default.removeItem(at: fileURL)
            return fileData
        }
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let key = Data(bytes)
        _ = saveKey(key)
        return key
    }
}
