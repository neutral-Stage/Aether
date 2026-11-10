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
            guard Self.isNewer(remote: latest, local: AetherConfig.appVersion) else {
                updateAvailable = nil
                return
            }
            let html = (json["body"] as? String) ?? ""
            let dmgAsset = (json["assets"] as? [[String: Any]])?
                .compactMap { $0["browser_download_url"] as? String }
                .first { $0.lowercased().hasSuffix(".dmg") }
                .flatMap(URL.init(string:))
            let page = (json["html_url"] as? String).flatMap(URL.init(string:))
                ?? dmgAsset
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

    nonisolated static func isNewer(remote: String, local: String) -> Bool {
        let (rCore, rPre) = parseSemver(remote)
        let (lCore, lPre) = parseSemver(local)
        let n = max(rCore.count, lCore.count)
        for i in 0 ..< n {
            let rv = i < rCore.count ? rCore[i] : 0
            let lv = i < lCore.count ? lCore[i] : 0
            if rv > lv { return true }
            if rv < lv { return false }
        }
        // Same core version: stable (empty pre-release) beats any pre-release (e.g. 1.0.0 > 1.0.0-rc.1).
        if rPre.isEmpty && !lPre.isEmpty { return true }
        if !rPre.isEmpty && lPre.isEmpty { return false }
        let m = max(rPre.count, lPre.count)
        for i in 0 ..< m {
            let rv = i < rPre.count ? rPre[i] : 0
            let lv = i < lPre.count ? lPre[i] : 0
            if rv > lv { return true }
            if rv < lv { return false }
        }
        return false
    }

    private nonisolated static func parseSemver(_ raw: String) -> (core: [Int], pre: [Int]) {
        let clean = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
        let parts = clean.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
        let core = parts[0].split(separator: ".").compactMap { Int($0) }
        guard parts.count > 1, !parts[1].isEmpty else {
            return (core, [])
        }
        let preDigits = parts[1].split(separator: ".").compactMap { piece -> Int? in
            let digits = piece.filter(\.isNumber)
            return digits.isEmpty ? nil : Int(digits)
        }
        return (core, preDigits.isEmpty ? [0] : preDigits)
    }
}
