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
/// - `POST /pim` — Calendar/Reminders/Contacts via `PIMService` (EventKit/
///   Contacts). Always on while the app runs, like `/capture`; requires the
///   bearer token and does not depend on `allowInvoke` — see
///   `docs/INTEGRATIONS.md`.
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
        if method == "POST" && path == "/pim" {
            // Personal data (Calendar/Reminders/Contacts): refuse outright when
            // no token exists, same as /capture — never gated by allowInvoke.
            guard let token = AetherConfig.nativeEffectorToken, !token.isEmpty else {
                return httpResponse(401, body: #"{"ok":false,"error":"pim requires a token"}"#)
            }
            guard let bodyStart = raw.range(of: "\r\n\r\n") else {
                return httpResponse(400, body: #"{"ok":false}"#)
            }
            let bodyStr = String(raw[bodyStart.upperBound...])
            guard let bodyData = bodyStr.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any] else {
                return httpResponse(400, body: #"{"ok":false,"error":"invalid json"}"#)
            }
            return await handlePIM(json)
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

    /// `POST /pim` dispatch: status, calendar/reminders/contacts reads, and the
    /// two confirmed writes. Access is checked here (not just inside
    /// `PIMService`) so every action gets the same 403 message before it ever
    /// touches EventKit/Contacts.
    private func handlePIM(_ json: [String: Any]) async -> String {
        guard let action = json["action"] as? String else {
            return jsonErrorResponse(400, "missing action")
        }
        let args = (json["args"] as? [String: Any]) ?? [:]
        let pim = PIMService.shared
        switch action {
        case "status":
            return jsonOK(pim.status())
        case "events":
            guard pimAccessOK("calendar") else { return pimAccessDenied("calendar") }
            guard let fromNum = args["from"] as? NSNumber, let toNum = args["to"] as? NSNumber else {
                return jsonErrorResponse(400, "from and to are required")
            }
            let start = Date(timeIntervalSince1970: fromNum.doubleValue)
            let end = Date(timeIntervalSince1970: toNum.doubleValue)
            let query = (args["query"] as? String) ?? ""
            let limit = (args["limit"] as? NSNumber)?.intValue ?? 20
            return jsonOK(pim.events(from: start, to: end, query: query, limit: limit))
        case "create_event":
            guard pimAccessOK("calendar", write: true) else { return pimAccessDenied("calendar") }
            guard let title = args["title"] as? String, !title.isEmpty,
                  let startNum = args["start"] as? NSNumber,
                  let endNum = args["end"] as? NSNumber else {
                return jsonErrorResponse(400, "title, start and end are required")
            }
            do {
                let row = try pim.createEvent(
                    title: title, start: Date(timeIntervalSince1970: startNum.doubleValue),
                    end: Date(timeIntervalSince1970: endNum.doubleValue),
                    location: args["location"] as? String, notes: args["notes"] as? String,
                    calendar: args["calendar"] as? String)
                return jsonOK(row)
            } catch {
                return jsonErrorResponse(500, error.localizedDescription)
            }
        case "reminders":
            guard pimAccessOK("reminders") else { return pimAccessDenied("reminders") }
            let list = args["list"] as? String
            let includeCompleted = (args["include_completed"] as? NSNumber)?.boolValue ?? false
            let limit = (args["limit"] as? NSNumber)?.intValue ?? 20
            let rows = await pim.reminders(list: list, includeCompleted: includeCompleted,
                                           limit: limit)
            return jsonOK(rows)
        case "add_reminder":
            guard pimAccessOK("reminders", write: true) else { return pimAccessDenied("reminders") }
            guard let title = args["title"] as? String, !title.isEmpty else {
                return jsonErrorResponse(400, "title is required")
            }
            let due = (args["due"] as? NSNumber).map { Date(timeIntervalSince1970: $0.doubleValue) }
            do {
                let row = try pim.addReminder(title: title, due: due,
                                              list: args["list"] as? String,
                                              notes: args["notes"] as? String)
                return jsonOK(row)
            } catch {
                return jsonErrorResponse(500, error.localizedDescription)
            }
        case "contacts":
            guard pimAccessOK("contacts") else { return pimAccessDenied("contacts") }
            let query = (args["query"] as? String) ?? ""
            let limit = (args["limit"] as? NSNumber)?.intValue ?? 10
            do {
                return jsonOK(try pim.contacts(query: query, limit: limit))
            } catch {
                return jsonErrorResponse(500, error.localizedDescription)
            }
        case "next_event":
            guard pimAccessOK("calendar") else { return pimAccessDenied("calendar") }
            let hours = (args["hours"] as? NSNumber)?.doubleValue ?? 24.0
            return jsonOK(pim.nextEvent(within: hours) ?? NSNull())
        default:
            return httpResponse(404, body: #"{"ok":false,"error":"unknown action"}"#)
        }
    }

    /// `write: true` also accepts `write_only` authorization (EventKit's
    /// create-without-read grant); reads need full `authorized`.
    private func pimAccessOK(_ kind: String, write: Bool = false) -> Bool {
        let status = PIMService.shared.status()[kind] ?? "not_determined"
        return write ? (status == "authorized" || status == "write_only") : status == "authorized"
    }

    private func pimAccessDenied(_ kind: String) -> String {
        let name: String
        switch kind {
        case "calendar": name = "Calendar"
        case "reminders": name = "Reminders"
        case "contacts": name = "Contacts"
        default: name = kind.capitalized
        }
        return jsonErrorResponse(403, "\(name) access isn't allowed — connect it in Aether → Integrations")
    }

    private func jsonOK(_ result: Any) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: ["ok": true, "result": result]) else {
            return httpResponse(500, body: #"{"ok":false,"error":"encoding failed"}"#)
        }
        return httpResponse(200, body: String(data: data, encoding: .utf8) ?? #"{"ok":true}"#)
    }

    private func jsonErrorResponse(_ status: Int, _ message: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: ["ok": false, "error": message]) else {
            return httpResponse(status, body: #"{"ok":false,"error":"error"}"#)
        }
        return httpResponse(status, body: String(data: data, encoding: .utf8) ?? #"{"ok":false}"#)
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
