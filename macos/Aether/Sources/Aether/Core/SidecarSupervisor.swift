import AppKit
import Foundation

/// Launches and supervises the Python sidecar so a shipped Aether.app is
/// self-contained — no terminal (Phase 7). Prefers a bundled frozen binary in
/// Resources; falls back to `python3 -m sidecar.server` from the repo (dev). If
/// a sidecar is already answering, it adopts it instead of spawning a second.
@MainActor
final class SidecarSupervisor: ObservableObject {
    enum Status: Equatable {
        case idle, starting, healthy, external, failed(String)
    }

    @Published private(set) var status: Status = .idle

    private var process: Process?
    private var restartCount = 0
    private var stopping = false
    private let baseURL = AetherConfig.sidecarBaseURL
    private var observer: NSObjectProtocol?

    func start() {
        if observer == nil {
            observer = NotificationCenter.default.addObserver(
                forName: NSApplication.willTerminateNotification, object: nil, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.stop() }
            }
        }
        Task {
            if await ping() { status = .external; return }
            status = .starting
            await launch()
        }
    }

    func restart() {
        stop()
        stopping = false
        restartCount = 0
        start()
    }

    func stop() {
        stopping = true
        process?.terminate()
        process = nil
    }

    // MARK: launch

    private func launch() async {
        guard let proc = makeProcess() else {
            status = .failed("sidecar not found — set AETHER_ROOT or bundle it")
            return
        }
        proc.terminationHandler = { [weak self] finished in
            guard let self else { return }
            Task { @MainActor in self.handleExit(finished) }
        }
        do {
            try proc.run()
            process = proc
        } catch {
            status = .failed("launch failed: \(error.localizedDescription)")
            return
        }
        for _ in 0..<40 {  // up to ~10s
            if await ping() {
                status = .healthy
                restartCount = 0
                return
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        status = .failed("sidecar did not become healthy")
    }

    private func handleExit(_ finished: Process) {
        if stopping { return }
        guard restartCount < 5 else {
            status = .failed("sidecar crashed repeatedly")
            return
        }
        restartCount += 1
        status = .starting
        Task {
            try? await Task.sleep(nanoseconds: UInt64(min(restartCount, 4)) * 500_000_000)
            if !stopping { await launch() }
        }
    }

    private func makeProcess() -> Process? {
        let proc = Process()
        var env = ProcessInfo.processInfo.environment
        for (key, value) in KeyStore.sidecarEnv() { env[key] = value }

        // 1) Bundled frozen sidecar in Resources (shipping build).
        if let resources = Bundle.main.resourceURL {
            let bundled = resources.appendingPathComponent("sidecar/aether-sidecar")
            if FileManager.default.isExecutableFile(atPath: bundled.path) {
                proc.executableURL = bundled
                proc.environment = env
                return proc
            }
        }

        // 2) Dev fallback: python3 -m sidecar.server from the repo root.
        guard let root = repoRoot() else { return nil }
        env["AETHER_ROOT"] = root
        let venvPython = root + "/.venv/bin/python3"
        let python = FileManager.default.isExecutableFile(atPath: venvPython)
            ? venvPython : "python3.11"
        proc.currentDirectoryURL = URL(fileURLWithPath: root)
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = [python, "-m", "sidecar.server"]
        proc.environment = env
        return proc
    }

    /// Find a dev checkout containing `sidecar/server.py`.
    private func repoRoot() -> String? {
        let fm = FileManager.default
        if let root = ProcessInfo.processInfo.environment["AETHER_ROOT"],
           !root.isEmpty,
           fm.fileExists(atPath: root + "/sidecar/server.py") {
            return root
        }
        var dir = URL(fileURLWithPath: Bundle.main.bundlePath)
        for _ in 0..<8 {
            if fm.fileExists(atPath: dir.appendingPathComponent("sidecar/server.py").path) {
                return dir.path
            }
            dir.deleteLastPathComponent()
        }
        let cwd = fm.currentDirectoryPath
        return fm.fileExists(atPath: cwd + "/sidecar/server.py") ? cwd : nil
    }

    private func ping() async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("health"))
        request.timeoutInterval = 1.0
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }
}
