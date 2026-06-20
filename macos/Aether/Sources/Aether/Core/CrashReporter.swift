import Foundation

private func aetherUncaughtExceptionHandler(_ exception: NSException) {
    let stack = exception.callStackSymbols.joined(separator: "\n")
    CrashReporter.postReport(
        message: exception.reason ?? exception.name.rawValue,
        stack: stack,
        fatal: true
    )
}

/// Opt-in crash and uncaught exception reporting to the sidecar (Phase 12).
enum CrashReporter {
    private static var installed = false

    static func installIfEnabled(_ enabled: Bool) {
        guard enabled, !installed else { return }
        installed = true
        NSSetUncaughtExceptionHandler(aetherUncaughtExceptionHandler)
    }

    static func reportError(_ error: Error, context: String = "") {
        postReport(
            message: "\(context): \(error.localizedDescription)",
            stack: Thread.callStackSymbols.joined(separator: "\n"),
            fatal: false
        )
    }

    static func postReport(message: String, stack: String, fatal: Bool) {
        let url = AetherConfig.sidecarBaseURL.appendingPathComponent("crash-report")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "message": message,
            "stack": stack,
            "app_version": AetherConfig.appVersion,
            "fatal": fatal,
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        if let token = AetherConfig.sidecarBearerToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: request).resume()
    }
}
