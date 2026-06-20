import AppKit
import Foundation
import Network

/// Minimal HTTP server for native click/type (Phase 8).
///
/// Sidecar calls `POST /invoke` when `beta.native_effectors: true`.
/// Hybrid path: Python effectors remain default — see `docs/NATIVE_EFFECTORS.md`.
@MainActor
final class NativeEffectorServer: ObservableObject {
    @Published var isRunning = false
    @Published var lastError: String?

    private var listener: NWListener?
    private let port: UInt16

    init(port: UInt16 = 8766) {
        self.port = port
    }

    func start() {
        guard !isRunning else { return }
        lastError = nil
        do {
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = true
            let listener = try NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
            listener.newConnectionHandler = { [weak self] connection in
                Task { @MainActor in
                    self?.handle(connection)
                }
            }
            listener.stateUpdateHandler = { [weak self] state in
                Task { @MainActor in
                    switch state {
                    case .ready:
                        self?.isRunning = true
                    case .failed(let err):
                        self?.isRunning = false
                        self?.lastError = err.localizedDescription
                    default:
                        break
                    }
                }
            }
            listener.start(queue: .global(qos: .utility))
            self.listener = listener
        } catch {
            lastError = error.localizedDescription
            isRunning = false
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
        isRunning = false
    }

    private func handle(_ connection: NWConnection) {
        connection.start(queue: .global(qos: .utility))
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, _, _ in
            guard let self, let data, !data.isEmpty else {
                connection.cancel()
                return
            }
            Task { @MainActor in
                let response = self.route(data)
                connection.send(content: response.data(using: .utf8), completion: .contentProcessed { _ in
                    connection.cancel()
                })
            }
        }
    }

    private func route(_ data: Data) -> String {
        guard let raw = String(data: data, encoding: .utf8) else {
            return httpResponse(400, body: #"{"ok":false,"error":"bad request"}"#)
        }
        if let authError = authorize(raw) {
            return authError
        }
        let lines = raw.split(separator: "\r\n", omittingEmptySubsequences: false)
        guard let requestLine = lines.first else {
            return httpResponse(400, body: #"{"ok":false}"#)
        }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else {
            return httpResponse(400, body: #"{"ok":false}"#)
        }
        let method = String(parts[0])
        let path = String(parts[1])
        if method == "GET" && path == "/health" {
            return httpResponse(200, body: #"{"ok":true,"service":"aether-native-effector"}"#)
        }
        guard method == "POST", path == "/invoke" else {
            return httpResponse(404, body: #"{"ok":false,"error":"not found"}"#)
        }
        guard let bodyStart = raw.range(of: "\r\n\r\n") else {
            return httpResponse(400, body: #"{"ok":false}"#)
        }
        let bodyStr = String(raw[bodyStart.upperBound...])
        guard let bodyData = bodyStr.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any],
              let tool = json["tool"] as? String,
              let args = json["args"] as? [String: Any] else {
            return httpResponse(400, body: #"{"ok":false,"error":"invalid json"}"#)
        }
        do {
            let result = try invoke(tool: tool, args: args)
            let payload = try JSONSerialization.data(withJSONObject: ["ok": true, "result": result])
            return httpResponse(200, body: String(data: payload, encoding: .utf8) ?? #"{"ok":true}"#)
        } catch {
            let payload = #"{"ok":false,"error":"\#(error.localizedDescription)"}"#
            return httpResponse(500, body: payload)
        }
    }

    private func invoke(tool: String, args: [String: Any]) throws -> String {
        switch tool {
        case "click":
            let x = (args["x"] as? NSNumber)?.doubleValue ?? 0
            let y = (args["y"] as? NSNumber)?.doubleValue ?? 0
            InputController.click(at: CGPoint(x: x, y: y))
            return "Clicked at (\(Int(x)), \(Int(y))) via Swift."
        case "type_text":
            let text = args["text"] as? String ?? ""
            InputController.typeText(text)
            return "Typed \(text.count) characters via Swift."
        default:
            throw NSError(domain: "Aether", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Unsupported native tool: \(tool)",
            ])
        }
    }

    private func httpResponse(_ status: Int, body: String) -> String {
        "HTTP/1.1 \(status) OK\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
    }

    private func authorize(_ raw: String) -> String? {
        guard let required = AetherConfig.nativeEffectorToken, !required.isEmpty else {
            return nil
        }
        let expected = "Bearer \(required)"
        for line in raw.split(separator: "\r\n") {
            let lower = line.lowercased()
            if lower.hasPrefix("authorization:") {
                let value = line.split(separator: ":", maxSplits: 1).dropFirst()
                    .joined(separator: ":")
                    .trimmingCharacters(in: .whitespaces)
                if value == expected {
                    return nil
                }
                return httpResponse(401, body: #"{"ok":false,"error":"unauthorized"}"#)
            }
        }
        return httpResponse(401, body: #"{"ok":false,"error":"missing authorization"}"#)
    }
}
