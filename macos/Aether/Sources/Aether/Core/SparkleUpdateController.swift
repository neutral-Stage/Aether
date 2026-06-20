import Foundation

/// Sparkle 2–ready update checker (Phase 9).
///
/// Production path: add Sparkle via SPM (`https://github.com/sparkle-project/Sparkle`)
/// and replace this stub with `SPUStandardUpdaterController`. Until then, we parse a
/// minimal appcast XML or fall back to the GitHub releases JSON feed.
@MainActor
final class SparkleUpdateController: ObservableObject {
    @Published var updateAvailable: UpdateInfo?
    @Published var lastCheckError: String?

    private let session: URLSession
    private let fallback: UpdateChecker

    init(session: URLSession = .shared) {
        self.session = session
        self.fallback = UpdateChecker(session: session)
    }

    func checkIfNeeded() async {
        guard AetherConfig.autoUpdateCheckEnabled else { return }
        await checkNow()
    }

    func checkNow() async {
        lastCheckError = nil
        updateAvailable = nil
        if let appcast = AetherConfig.sparkleAppcastURL {
            await checkAppcast(url: appcast)
            if updateAvailable != nil {
                return
            }
            if lastCheckError == nil {
                return
            }
        }
        await fallback.checkNow()
        updateAvailable = fallback.updateAvailable
        lastCheckError = fallback.lastCheckError
    }

    private func checkAppcast(url: URL) async {
        guard url.scheme == "https" || url.scheme == "http" else {
            lastCheckError = "Invalid appcast URL scheme"
            return
        }
        do {
            let (data, response) = try await session.data(from: url)
            guard let http = response as? HTTPURLResponse else {
                lastCheckError = "Appcast unreachable"
                return
            }
            guard (200 ... 299).contains(http.statusCode) else {
                lastCheckError = "Appcast HTTP \(http.statusCode)"
                return
            }
            guard !data.isEmpty else {
                lastCheckError = "Empty appcast response"
                return
            }
            guard let xml = String(data: data, encoding: .utf8), !xml.isEmpty else {
                lastCheckError = "Invalid appcast encoding"
                return
            }
            guard xml.contains("<rss") || xml.contains("<item") else {
                lastCheckError = "Malformed appcast (not RSS/XML)"
                return
            }
            guard let version = parseAppcastVersion(xml) else {
                lastCheckError = "Malformed appcast (missing version)"
                return
            }
            guard isNewer(remote: version, local: AetherConfig.appVersion) else {
                updateAvailable = nil
                return
            }
            guard let enclosure = parseAppcastEnclosure(xml) else {
                lastCheckError = "Malformed appcast (missing enclosure)"
                return
            }
            updateAvailable = UpdateInfo(
                latestVersion: version,
                releaseURL: enclosure,
                notes: "Update available via Sparkle appcast."
            )
        } catch {
            lastCheckError = error.localizedDescription
        }
    }

    private func parseAppcastVersion(_ xml: String) -> String? {
        let tags = ["<sparkle:shortVersionString>", "<sparkle:version>"]
        for tag in tags {
            guard let range = xml.range(of: tag) else { continue }
            let tail = xml[range.upperBound...]
            let close = tag.replacingOccurrences(of: "<", with: "</")
            guard let end = tail.range(of: close) else { continue }
            let value = String(tail[..<end.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !value.isEmpty { return value }
        }
        return nil
    }

    private func parseAppcastEnclosure(_ xml: String) -> URL? {
        guard let urlRange = xml.range(of: "url=\"") else { return nil }
        let tail = xml[urlRange.upperBound...]
        guard let endQuote = tail.firstIndex(of: "\"") else { return nil }
        let raw = String(tail[..<endQuote])
        guard let url = URL(string: raw), url.scheme != nil else { return nil }
        return url
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
