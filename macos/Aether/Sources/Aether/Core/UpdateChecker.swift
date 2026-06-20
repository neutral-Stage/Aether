import Foundation

struct UpdateInfo: Equatable {
    let latestVersion: String
    let releaseURL: URL
    let notes: String
}

/// Minimal update checker — compares local version to a GitHub releases feed (Phase 5).
@MainActor
final class UpdateChecker: ObservableObject {
    @Published var updateAvailable: UpdateInfo?
    @Published var lastCheckError: String?

    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func checkIfNeeded() async {
        guard AetherConfig.autoUpdateCheckEnabled else { return }
        await checkNow()
    }

    func checkNow() async {
        lastCheckError = nil
        updateAvailable = nil
        let url = AetherConfig.updateFeedURL
        guard url.scheme == "https" || url.scheme == "http" else {
            lastCheckError = "Invalid update feed URL"
            return
        }
        do {
            let (data, response) = try await session.data(from: url)
            guard let http = response as? HTTPURLResponse else {
                lastCheckError = "Update feed unreachable"
                return
            }
            guard (200 ... 299).contains(http.statusCode) else {
                lastCheckError = "Update feed HTTP \(http.statusCode)"
                return
            }
            guard !data.isEmpty else {
                lastCheckError = "Empty update feed"
                return
            }
            // Appcast misconfigured as JSON feed — hint to use Sparkle path.
            if let text = String(data: data, encoding: .utf8),
               text.contains("<rss") || text.contains("sparkle:shortVersionString") {
                lastCheckError = "Feed is Sparkle appcast XML; set AETHER_SPARKLE_APPCAST_URL"
                return
            }
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                lastCheckError = "Invalid release JSON"
                return
            }
            guard let tag = json["tag_name"] as? String, !tag.isEmpty else {
                lastCheckError = "Malformed release JSON (missing tag_name)"
                return
            }
            let latest = tag.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
            guard isNewer(remote: latest, local: AetherConfig.appVersion) else {
                updateAvailable = nil
                return
            }
            let html = (json["body"] as? String) ?? ""
            let page = (json["html_url"] as? String).flatMap(URL.init(string:))
                ?? url.deletingLastPathComponent()
            updateAvailable = UpdateInfo(
                latestVersion: latest,
                releaseURL: page,
                notes: String(html.prefix(280))
            )
        } catch {
            lastCheckError = error.localizedDescription
        }
    }

    private func isNewer(remote: String, local: String) -> Bool {
        let r = remote.split(separator: ".").compactMap { Int($0) }
        let l = local.split(separator: ".").compactMap { Int($0) }
        let n = max(r.count, l.count)
        for i in 0 ..< n {
            let rv = i < r.count ? r[i] : 0
            let lv = i < l.count ? l[i] : 0
            if rv > lv { return true }
            if rv < lv { return false }
        }
        return false
    }
}
