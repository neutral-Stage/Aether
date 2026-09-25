import Foundation
import SwiftUI

/// A calendar event worth showing in the menu bar: not all-day, with a title, and
/// ending after it starts. Built from the JSON `PIMService.nextEvent(within:)`
/// returns, so it can be tested without EventKit or the main actor.
struct UpcomingMeeting: Equatable {
    let title: String
    let start: Date
    let end: Date

    /// `nil` for a missing dict, an all-day event, a missing/empty title, a
    /// missing/bad start or end, or an end that isn't after the start.
    static func from(_ json: [String: Any]?) -> UpcomingMeeting? {
        guard let json else { return nil }
        guard json["all_day"] as? Bool != true else { return nil }
        guard let title = json["title"] as? String, !title.isEmpty else { return nil }
        guard let startSeconds = json["start"] as? Double,
              let endSeconds = json["end"] as? Double else { return nil }
        let start = Date(timeIntervalSince1970: startSeconds)
        let end = Date(timeIntervalSince1970: endSeconds)
        guard end > start else { return nil }
        return UpcomingMeeting(title: title, start: start, end: end)
    }
}

/// Whether — and how — to show the next meeting. Pure and `nonisolated`: no
/// AppKit, no PIMService, so it is testable with fixed dates.
enum NextMeetingPolicy {
    /// A meeting only worth showing once it is running, or starts within the
    /// next hour; `nil` once it has ended.
    static func shown(_ meeting: UpcomingMeeting?, now: Date) -> UpcomingMeeting? {
        guard let meeting, meeting.end > now else { return nil }
        if meeting.start <= now { return meeting }
        return meeting.start.timeIntervalSince(now) <= 3600 ? meeting : nil
    }

    /// True from 10 minutes before the meeting starts, and while it runs.
    static func offersNotes(_ meeting: UpcomingMeeting, now: Date) -> Bool {
        if meeting.start <= now { return now < meeting.end }
        return meeting.start.timeIntervalSince(now) <= 600
    }

    /// "Next: Budget sync · 14:00 (in 25 min)" before it starts (or "(now)" in the
    /// last minute), "Now: Budget sync · until 14:30" while it runs.
    static func label(_ meeting: UpcomingMeeting, now: Date, locale: Locale = .current,
                      timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        formatter.locale = locale
        formatter.timeZone = timeZone
        let title = shortTitle(meeting.title)
        if meeting.start <= now {
            return "Now: \(title) · until \(formatter.string(from: meeting.end))"
        }
        let secondsUntil = meeting.start.timeIntervalSince(now)
        let startTime = formatter.string(from: meeting.start)
        guard secondsUntil >= 60 else {
            return "Next: \(title) · \(startTime) (now)"
        }
        let minutes = Int((secondsUntil / 60).rounded(.up))
        return "Next: \(title) · \(startTime) (in \(minutes) min)"
    }

    /// `title`, cut to `limit` characters with "…" so the menu stays narrow.
    static func shortTitle(_ title: String, limit: Int = 40) -> String {
        guard title.count > limit else { return title }
        return String(title.prefix(limit)) + "…"
    }
}

/// The next calendar meeting, polled for the menu bar. Reads only through
/// `PIMService`, which never prompts on its own — `nextEvent` fails closed when
/// Calendar isn't authorized — and only when the sidecar's Calendar integration
/// is turned on, so nothing is read unless both say yes.
@MainActor
final class NextMeetingController: ObservableObject {
    @Published private(set) var meeting: UpcomingMeeting?
    @Published private(set) var now = Date()

    private let client: OrchestratorClient
    private var pollTask: Task<Void, Never>?
    private var calendarEnabled = false
    private var calendarEnabledCheckedAt: Date?
    /// How often the sidecar's integrations list is re-checked.
    private let calendarEnabledTTL: TimeInterval = 300
    /// How far ahead `PIMService.nextEvent` looks.
    private let horizonHours = 12.0

    init(client: OrchestratorClient) {
        self.client = client
    }

    /// Updates `meeting`/`now`. The sidecar's "calendar" integration flag is
    /// cached and re-fetched at most every 5 minutes, unless `force` is set.
    func refresh(force: Bool = false) async {
        if force || calendarEnabledCheckedAt == nil
            || Date().timeIntervalSince(calendarEnabledCheckedAt!) >= calendarEnabledTTL {
            let integrations = await client.fetchIntegrations()
            calendarEnabled = integrations.contains {
                ($0["id"] as? String) == "calendar" && ($0["enabled"] as? Bool) == true
            }
            calendarEnabledCheckedAt = Date()
        }
        guard calendarEnabled, PIMService.shared.status()["calendar"] == "authorized" else {
            meeting = nil
            return
        }
        let current = Date()
        meeting = NextMeetingPolicy.shown(
            UpcomingMeeting.from(PIMService.shared.nextEvent(within: horizonHours)), now: current)
        now = current
    }

    /// Polls every 60 s; safe to call more than once, stopped by `stop()`.
    func start() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                try? await Task.sleep(nanoseconds: 60_000_000_000)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }
}
