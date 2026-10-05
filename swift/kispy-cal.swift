// Reads the timetable through EventKit: whatever macOS has already synced, with
// no OAuth and no token to refresh. Prints JSON on stdout.
//
//   kispy-cal                    every event of the academic year, as JSON
//   kispy-cal --list             the calendars this Mac knows about
//   kispy-cal --since 2025-09-01 start the window on a given date
import Foundation
import EventKit

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false

if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; sem.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; sem.signal() }
}
_ = sem.wait(timeout: .now() + 60)

guard granted else {
    FileHandle.standardError.write(Data(
        "kispy-cal: calendar access refused — System Settings > Privacy & Security > Calendars\n".utf8))
    exit(2)
}

let cals = store.calendars(for: .event)
let args = CommandLine.arguments
if args.contains("--list") {
    for c in cals { print("\(c.title)\t\(c.source.title)") }
    exit(0)
}

// The window must cover the sessions already taught, otherwise "Lecture N"
// cannot be counted. September opens the year unless told otherwise.
let cal = Calendar.current
let now = Date()
var from: Date
if let i = args.firstIndex(of: "--since"), i + 1 < args.count {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    f.timeZone = TimeZone.current
    from = f.date(from: args[i + 1]) ?? cal.date(byAdding: .month, value: -10, to: now)!
} else {
    let year = cal.component(.month, from: now) >= 9
        ? cal.component(.year, from: now)
        : cal.component(.year, from: now) - 1
    from = cal.date(from: DateComponents(year: year, month: 9, day: 1))!
}
let to = cal.date(byAdding: .day, value: 30, to: now)!

// EventKit refuses a predicate longer than four years, and a long span is slow:
// ask for it in chunks of a year.
var events: [EKEvent] = []
var cursor = from
while cursor < to {
    let next = min(cal.date(byAdding: .year, value: 1, to: cursor)!, to)
    events += store.events(matching: store.predicateForEvents(withStart: cursor, end: next,
                                                             calendars: cals))
    cursor = next
}

let iso = ISO8601DateFormatter()
iso.formatOptions = [.withInternetDateTime]
var seen = Set<String>()
var rows: [[String: Any]] = []
for e in events where !e.isAllDay {
    let key = "\(e.calendarItemIdentifier)|\(iso.string(from: e.startDate))"
    if seen.contains(key) { continue }
    seen.insert(key)
    rows.append([
        "summary": e.title ?? "",
        "start": iso.string(from: e.startDate),
        "end": iso.string(from: e.endDate),
        "location": e.location ?? "",
        "calendar": e.calendar.title,
    ])
}
let data = try JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
