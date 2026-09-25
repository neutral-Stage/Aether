import Contacts
import EventKit
import Foundation

/// Calendar, Reminders and Contacts, read (and Calendar/Reminders, written)
/// through EventKit/Contacts. The app owns the macOS permission prompts —
/// `requestAccess` is only ever called from the Integrations panel, never
/// implicitly by a read. Every method fails closed: when access isn't
/// granted it returns an empty/`nil` result (or throws, for the ones that
/// declare `throws`) instead of letting EventKit/Contacts show their own
/// prompt. `POST /pim` on `NativeEffectorServer` is the only caller outside
/// the app itself (the Python sidecar's `aether/ipc/native_effector.py`).
@MainActor
final class PIMService {
    static let shared = PIMService()

    let eventStore = EKEventStore()
    let contactStore = CNContactStore()

    private init() {}

    // MARK: status / access

    func status() -> [String: String] {
        [
            "calendar": Self.ekStatusString(EKEventStore.authorizationStatus(for: .event)),
            "reminders": Self.ekStatusString(EKEventStore.authorizationStatus(for: .reminder)),
            "contacts": Self.cnStatusString(CNContactStore.authorizationStatus(for: .contacts)),
        ]
    }

    /// Triggers the macOS permission prompt for `kind` ("calendar" | "reminders" |
    /// "contacts"). False on refusal, an unknown kind, or an EventKit/Contacts error.
    func requestAccess(_ kind: String) async -> Bool {
        switch kind {
        case "calendar":
            return (try? await eventStore.requestFullAccessToEvents()) ?? false
        case "reminders":
            return (try? await eventStore.requestFullAccessToReminders()) ?? false
        case "contacts":
            return await withCheckedContinuation { continuation in
                contactStore.requestAccess(for: .contacts) { granted, _ in
                    continuation.resume(returning: granted)
                }
            }
        default:
            return false
        }
    }

    private static func ekStatusString(_ status: EKAuthorizationStatus) -> String {
        switch status {
        case .fullAccess, .authorized:   // .authorized is the pre-macOS-14 case; same meaning
            return "authorized"
        case .writeOnly:
            return "write_only"
        case .denied:
            return "denied"
        case .restricted:
            return "restricted"
        case .notDetermined:
            return "not_determined"
        @unknown default:
            return "not_determined"
        }
    }

    private static func cnStatusString(_ status: CNAuthorizationStatus) -> String {
        switch status {
        case .authorized:
            return "authorized"
        case .denied:
            return "denied"
        case .restricted:
            return "restricted"
        case .notDetermined:
            return "not_determined"
        @unknown default:
            return "not_determined"
        }
    }

    // MARK: calendar

    func events(from start: Date, to end: Date, query: String, limit: Int) -> [[String: Any]] {
        guard Self.ekStatusString(EKEventStore.authorizationStatus(for: .event)) == "authorized" else {
            return []
        }
        let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
        var found = eventStore.events(matching: predicate)
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !needle.isEmpty {
            found = found.filter { ev in
                (ev.title ?? "").lowercased().contains(needle)
                    || (ev.location ?? "").lowercased().contains(needle)
                    || (ev.notes ?? "").lowercased().contains(needle)
            }
        }
        found.sort { $0.startDate < $1.startDate }
        return found.prefix(max(0, limit)).map { ev in
            Self.eventJSON(title: ev.title ?? "", start: ev.startDate, end: ev.endDate,
                           allDay: ev.isAllDay, location: ev.location,
                           calendar: ev.calendar?.title, notes: ev.notes, id: ev.eventIdentifier)
        }
    }

    func createEvent(title: String, start: Date, end: Date, location: String?, notes: String?,
                     calendar: String?) throws -> [String: Any] {
        let status = Self.ekStatusString(EKEventStore.authorizationStatus(for: .event))
        guard status == "authorized" || status == "write_only" else {
            throw Self.accessError("Calendar")
        }
        let event = EKEvent(eventStore: eventStore)
        event.title = title
        event.startDate = start
        event.endDate = end
        event.location = (location?.isEmpty == false) ? location : nil
        event.notes = (notes?.isEmpty == false) ? notes : nil
        if let calendarName = calendar, !calendarName.isEmpty,
           let match = eventStore.calendars(for: .event).first(where: {
               $0.title == calendarName && $0.allowsContentModifications
           }) {
            event.calendar = match
        } else if let def = eventStore.defaultCalendarForNewEvents {
            event.calendar = def
        } else {
            throw Self.noWritableCalendarError("calendar")
        }
        try eventStore.save(event, span: .thisEvent)
        return Self.eventJSON(title: event.title ?? title, start: event.startDate,
                              end: event.endDate, allDay: event.isAllDay,
                              location: event.location, calendar: event.calendar?.title,
                              notes: event.notes, id: event.eventIdentifier)
    }

    /// The non-all-day event, running now or starting within `hours`, whose start is
    /// nearest to now (see `nearestEvent`).
    func nextEvent(within hours: Double) -> [String: Any]? {
        guard Self.ekStatusString(EKEventStore.authorizationStatus(for: .event)) == "authorized" else {
            return nil
        }
        let now = Date()
        let horizon = now.addingTimeInterval(max(0, hours) * 3600)
        let predicate = eventStore.predicateForEvents(withStart: now, end: horizon, calendars: nil)
        let next = Self.nearestEvent(eventStore.events(matching: predicate), now: now,
                                     start: { $0.startDate }, end: { $0.endDate },
                                     allDay: { $0.isAllDay })
        guard let next else { return nil }
        return Self.eventJSON(title: next.title ?? "", start: next.startDate, end: next.endDate,
                              allDay: next.isAllDay, location: next.location,
                              calendar: next.calendar?.title, notes: next.notes,
                              id: next.eventIdentifier)
    }

    /// Of `items`, the non-all-day one that hasn't ended and whose start is nearest
    /// `now` — so a meeting about to begin wins over a long block that began hours ago.
    nonisolated static func nearestEvent<T>(_ items: [T], now: Date, start: (T) -> Date,
                                            end: (T) -> Date, allDay: (T) -> Bool) -> T? {
        items.filter { !allDay($0) && end($0) > now }
            .min { abs(start($0).timeIntervalSince(now)) < abs(start($1).timeIntervalSince(now)) }
    }

    // MARK: reminders

    func reminders(list: String?, includeCompleted: Bool, limit: Int) async -> [[String: Any]] {
        guard Self.ekStatusString(EKEventStore.authorizationStatus(for: .reminder)) == "authorized" else {
            return []
        }
        var calendars: [EKCalendar]?
        if let listName = list, !listName.isEmpty {
            calendars = eventStore.calendars(for: .reminder).filter { $0.title == listName }
        }
        let predicate: NSPredicate = includeCompleted
            ? eventStore.predicateForReminders(in: calendars)
            : eventStore.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil,
                                                          calendars: calendars)
        let found: [EKReminder] = await withCheckedContinuation { continuation in
            eventStore.fetchReminders(matching: predicate) { reminders in
                continuation.resume(returning: reminders ?? [])
            }
        }
        let sorted = found.sorted { lhs, rhs in
            let l = lhs.dueDateComponents?.date ?? Date.distantFuture
            let r = rhs.dueDateComponents?.date ?? Date.distantFuture
            return l < r
        }
        return sorted.prefix(max(0, limit)).map { rem in
            Self.reminderJSON(title: rem.title ?? "", due: rem.dueDateComponents?.date,
                              completed: rem.isCompleted, list: rem.calendar?.title,
                              notes: rem.notes, id: rem.calendarItemIdentifier)
        }
    }

    func addReminder(title: String, due: Date?, list: String?, notes: String?) throws -> [String: Any] {
        let status = Self.ekStatusString(EKEventStore.authorizationStatus(for: .reminder))
        guard status == "authorized" || status == "write_only" else {
            throw Self.accessError("Reminders")
        }
        let reminder = EKReminder(eventStore: eventStore)
        reminder.title = title
        reminder.notes = (notes?.isEmpty == false) ? notes : nil
        if let due {
            reminder.dueDateComponents = Calendar.current.dateComponents(
                [.year, .month, .day, .hour, .minute, .second], from: due)
        }
        if let listName = list, !listName.isEmpty,
           let match = eventStore.calendars(for: .reminder).first(where: {
               $0.title == listName && $0.allowsContentModifications
           }) {
            reminder.calendar = match
        } else if let def = eventStore.defaultCalendarForNewReminders() {
            reminder.calendar = def
        } else {
            throw Self.noWritableCalendarError("reminders list")
        }
        try eventStore.save(reminder, commit: true)
        return Self.reminderJSON(title: reminder.title ?? title,
                                 due: reminder.dueDateComponents?.date,
                                 completed: reminder.isCompleted, list: reminder.calendar?.title,
                                 notes: reminder.notes, id: reminder.calendarItemIdentifier)
    }

    // MARK: contacts

    func contacts(query: String, limit: Int) throws -> [[String: Any]] {
        guard Self.cnStatusString(CNContactStore.authorizationStatus(for: .contacts)) == "authorized" else {
            throw Self.accessError("Contacts")
        }
        let keys: [CNKeyDescriptor] = [
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactOrganizationNameKey as CNKeyDescriptor,
            CNContactEmailAddressesKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let predicate = CNContact.predicateForContacts(matchingName: query)
        let found = try contactStore.unifiedContacts(matching: predicate, keysToFetch: keys)
        return found.prefix(max(0, limit)).map { c in
            let name = [c.givenName, c.familyName].filter { !$0.isEmpty }.joined(separator: " ")
            let emails = c.emailAddresses.map { $0.value as String }
            let phones = c.phoneNumbers.map { $0.value.stringValue }
            return Self.contactJSON(
                name: name.isEmpty ? c.organizationName : name,
                organization: c.organizationName, emails: emails, phones: phones)
        }
    }

    // MARK: pure JSON builders (unit-testable without permissions)
    //
    // `nonisolated` so tests can call them directly without hopping to the
    // main actor — they touch no PIMService state, EventKit or Contacts.

    nonisolated static func eventJSON(title: String, start: Date, end: Date, allDay: Bool,
                                      location: String?, calendar: String?, notes: String?,
                                      id: String?) -> [String: Any] {
        var json: [String: Any] = [
            "title": title,
            "start": start.timeIntervalSince1970,
            "end": end.timeIntervalSince1970,
            "all_day": allDay,
        ]
        if let location, !location.isEmpty { json["location"] = location }
        if let calendar, !calendar.isEmpty { json["calendar"] = calendar }
        if let notes, !notes.isEmpty { json["notes"] = String(notes.prefix(500)) }
        if let id, !id.isEmpty { json["id"] = id }
        return json
    }

    nonisolated static func reminderJSON(title: String, due: Date?, completed: Bool, list: String?,
                                         notes: String?, id: String?) -> [String: Any] {
        var json: [String: Any] = ["title": title, "completed": completed]
        if let due { json["due"] = due.timeIntervalSince1970 }
        if let list, !list.isEmpty { json["list"] = list }
        if let notes, !notes.isEmpty { json["notes"] = String(notes.prefix(500)) }
        if let id, !id.isEmpty { json["id"] = id }
        return json
    }

    nonisolated static func contactJSON(name: String, organization: String?, emails: [String],
                                        phones: [String]) -> [String: Any] {
        var json: [String: Any] = ["name": name, "emails": emails, "phones": phones]
        if let organization, !organization.isEmpty { json["organization"] = organization }
        return json
    }

    private static func accessError(_ what: String) -> NSError {
        NSError(domain: "Aether", code: 403, userInfo: [
            NSLocalizedDescriptionKey: "\(what) access isn't allowed — connect it in Aether → Integrations",
        ])
    }

    private static func noWritableCalendarError(_ what: String) -> NSError {
        NSError(domain: "Aether", code: 500, userInfo: [
            NSLocalizedDescriptionKey: "No writable \(what) available.",
        ])
    }
}
