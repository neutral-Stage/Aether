import AppKit
import Foundation
import Network

/// Minimal loopback HTTP server the sidecar uses for native capabilities.
///
/// - `GET /health` — liveness (no auth).
/// - `GET /capture?display_id=&max_edge=` — ScreenCaptureKit screenshot of one
///   display with Aether's own windows hidden. Always on while the app runs;
///   requires the bearer token.
/// - `POST /invoke` — native click/type, only when `beta.native_effectors` is
///   on (`allowInvoke`). Python effectors remain the default — see
///   `docs/NATIVE_EFFECTORS.md`.
@MainActor
final class NativeEffectorServer: ObservableObject {
    @Published var isRunning = false
    @Published var lastError: String?
    /// Mirrors `beta.native_effectors`; gates `POST /invoke`.
    var allowInvoke = false

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
            // Loopback only: this server hands out screenshots and synthesizes
            // input, so it must never be reachable from the network.
            params.requiredLocalEndpoint = NWEndpoint.hostPort(
                host: .ipv4(.loopback), port: NWEndpoint.Port(rawValue: port)!)
            let listener = try NWListener(using: params)
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
                let response = await self.route(data)
                connection.send(content: response.data(using: .utf8), completion: .contentProcessed { _ in
                    connection.cancel()
                })
            }
        }
    }

    private func route(_ data: Data) async -> String {
        guard let raw = String(data: data, encoding: .utf8) else {
            return httpResponse(400, body: #"{"ok":false,"error":"bad request"}"#)
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
        let target = String(parts[1])
        let components = URLComponents(string: "http://localhost\(target)")
        let path = components?.path ?? target
        if method == "GET" && path == "/health" {
            return httpResponse(200, body: #"{"ok":true,"service":"aether-native-effector"}"#)
        }
        if let authError = authorize(raw) {
            return authError
        }
        if method == "GET" && path == "/capture" {
            // Screenshots are sensitive: refuse outright when no token exists.
            guard let token = AetherConfig.nativeEffectorToken, !token.isEmpty else {
                return httpResponse(401, body: #"{"ok":false,"error":"capture requires a token"}"#)
            }
            return await capture(query: components?.queryItems ?? [])
        }
        guard method == "POST", path == "/invoke" else {
            return httpResponse(404, body: #"{"ok":false,"error":"not found"}"#)
        }
        guard allowInvoke else {
            return httpResponse(403, body: #"{"ok":false,"error":"native effectors disabled"}"#)
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

    private func capture(query: [URLQueryItem]) async -> String {
        let displayID = query.first(where: { $0.name == "display_id" })?.value.flatMap { UInt32($0) }
        let maxEdge = query.first(where: { $0.name == "max_edge" })?.value.flatMap { Int($0) } ?? 0
        do {
            let r = try await ScreenCapture.capture(displayID: displayID, maxEdge: maxEdge)
            let payload: [String: Any] = [
                "ok": true,
                "path": r.url.path,
                "width": r.width,
                "height": r.height,
                "display_id": Int(r.displayID),
                "scale": r.scale,
                "frame": [Double(r.frame.origin.x), Double(r.frame.origin.y),
                          Double(r.frame.width), Double(r.frame.height)],
            ]
            let body = try JSONSerialization.data(withJSONObject: payload)
            return httpResponse(200, body: String(data: body, encoding: .utf8) ?? #"{"ok":false}"#)
        } catch {
            let message = error.localizedDescription.replacingOccurrences(of: "\"", with: "'")
            return httpResponse(500, body: #"{"ok":false,"error":"\#(message)"}"#)
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
