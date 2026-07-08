import Foundation
import Security

/// Porcupine wake-word engine via `dlopen` of Picovoice's C library.
///
/// No SPM dependency: we resolve `libpv_porcupine`'s C ABI at runtime, so the
/// app builds and ships without the binary. When the user installs the library
/// and provides an access key + keyword file, this becomes a real, low-latency
/// wake-word detector; otherwise ``isAvailable`` is false and the caller falls
/// back to the energy heuristic.
///
/// Setup (see docs/VOICE.md):
///   1. Install libpv_porcupine.dylib (Picovoice SDK) somewhere on the search
///      path or set `PORCUPINE_LIB_PATH`.
///   2. Store the access key in the Keychain (service `com.aether.porcupine`)
///      or set `PORCUPINE_ACCESS_KEY`.
///   3. Point `PORCUPINE_KEYWORD_PATH` / `PORCUPINE_MODEL_PATH` at the .ppn/.pv
///      files (a bundled "Hey Aether" keyword can live in Resources).
final class PorcupineWakeWord {

    // C signatures resolved from the dylib. Targets the Porcupine v3.x C ABI:
    //   pv_porcupine_init(access_key, model_path, device, num_keywords,
    //                     keyword_paths, sensitivities, object)
    // `device` (nil = auto-select) sits between model_path and num_keywords.
    private typealias InitFn = @convention(c) (
        UnsafePointer<CChar>?, UnsafePointer<CChar>?, UnsafePointer<CChar>?, Int32,
        UnsafePointer<UnsafePointer<CChar>?>?, UnsafePointer<Float>?,
        UnsafeMutablePointer<OpaquePointer?>?
    ) -> Int32
    private typealias ProcessFn = @convention(c) (
        OpaquePointer?, UnsafePointer<Int16>?, UnsafeMutablePointer<Int32>?
    ) -> Int32
    private typealias DeleteFn = @convention(c) (OpaquePointer?) -> Void
    private typealias FrameLenFn = @convention(c) () -> Int32
    private typealias SampleRateFn = @convention(c) () -> Int32

    private var handle: UnsafeMutableRawPointer?
    private var porcupine: OpaquePointer?
    private var processFn: ProcessFn?
    private var deleteFn: DeleteFn?

    private(set) var frameLength = 512
    private(set) var sampleRate = 16000
    private(set) var isAvailable = false

    init?() {
        guard let key = Self.accessKey(),
              let keywordPath = Self.env("PORCUPINE_KEYWORD_PATH"),
              FileManager.default.fileExists(atPath: keywordPath) else {
            return nil
        }
        guard let lib = Self.openLibrary() else { return nil }
        handle = lib
        guard let initFn = Self.sym(lib, "pv_porcupine_init", as: InitFn.self),
              let processFn = Self.sym(lib, "pv_porcupine_process", as: ProcessFn.self),
              let deleteFn = Self.sym(lib, "pv_porcupine_delete", as: DeleteFn.self),
              let frameLenFn = Self.sym(lib, "pv_porcupine_frame_length", as: FrameLenFn.self),
              let rateFn = Self.sym(lib, "pv_sample_rate", as: SampleRateFn.self)
        else { return nil }

        let modelPath = Self.env("PORCUPINE_MODEL_PATH") ?? ""
        let sensitivity: Float = 0.5
        var pp: OpaquePointer?
        let status: Int32 = key.withCString { keyC in
            modelPath.withCString { modelC in
                keywordPath.withCString { kwC in
                    var kwPtr: UnsafePointer<CChar>? = kwC
                    var sens = sensitivity
                    return withUnsafePointer(to: &kwPtr) { kwArr in
                        withUnsafePointer(to: &sens) { sensPtr in
                            initFn(keyC, modelPath.isEmpty ? nil : modelC, nil, 1,
                                   kwArr, sensPtr, &pp)
                        }
                    }
                }
            }
        }
        guard status == 0, let pp else { return nil }

        self.porcupine = pp
        self.processFn = processFn
        self.deleteFn = deleteFn
        self.frameLength = Int(frameLenFn())
        self.sampleRate = Int(rateFn())
        self.isAvailable = true
    }

    deinit {
        if let porcupine, let deleteFn { deleteFn(porcupine) }
        if let handle { dlclose(handle) }
    }

    /// Feed exactly `frameLength` Int16 samples; returns true on wake.
    func process(_ frame: [Int16]) -> Bool {
        guard isAvailable, let porcupine, let processFn,
              frame.count == frameLength else { return false }
        var keywordIndex: Int32 = -1
        let status = frame.withUnsafeBufferPointer {
            processFn(porcupine, $0.baseAddress, &keywordIndex)
        }
        return status == 0 && keywordIndex >= 0
    }

    // MARK: - resolution helpers

    private static func env(_ name: String) -> String? {
        let v = ProcessInfo.processInfo.environment[name]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (v?.isEmpty ?? true) ? nil : v
    }

    private static func accessKey() -> String? {
        if let e = env("PORCUPINE_ACCESS_KEY") { return e }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.aether.porcupine",
            kSecAttrAccount as String: "access-key",
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let key = String(data: data, encoding: .utf8), !key.isEmpty
        else { return nil }
        return key
    }

    private static func openLibrary() -> UnsafeMutableRawPointer? {
        var names = ["libpv_porcupine.dylib", "libpv_porcupine.so", "libpv_porcupine"]
        if let explicit = env("PORCUPINE_LIB_PATH") { names.insert(explicit, at: 0) }
        for name in names {
            if let h = dlopen(name, RTLD_NOW) { return h }
        }
        return nil
    }

    private static func sym<T>(_ lib: UnsafeMutableRawPointer, _ name: String,
                               as _: T.Type) -> T? {
        guard let sym = dlsym(lib, name) else { return nil }
        return unsafeBitCast(sym, to: T.self)
    }
}
