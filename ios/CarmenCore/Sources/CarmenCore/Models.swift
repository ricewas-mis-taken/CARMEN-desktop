import Foundation

public enum LockMode: String, Codable, CaseIterable, Identifiable {
    case soft, hard
    public var id: String { rawValue }
    public var title: String { self == .soft ? "Soft lock" : "Hard lock" }
}

/// A task as the desktop stores it (tasks_store.DEFAULT_TASK). Only what the phone shows is decoded;
/// `extra` keeps every other field so editing a task here never drops what the desktop wrote.
public struct CarmenTask: Identifiable, Equatable {
    public var id: String
    public var name: String
    public var color: String
    public var targetMinutes: Int
    public var recurrence: String
    public var weekdays: [String]
    public var dayMinutes: [String: Int]
    public var lockMode: LockMode
    public var domainWhitelist: [String]
    public var archived: Bool
    public var updatedAt: Date?
    public var deviceId: String
    public var isDeleted: Bool
    public var extra: [String: JSONValue]

    static let known: Set<String> = ["name", "color", "targetMinutes", "recurrence", "weekdays", "dayMinutes",
                                     "lockMode", "domainWhitelist", "archived"]

    public init(record: SyncRecord) {
        let data = record.data.object ?? [:]
        id = record.syncId
        name = data["name"]?.string ?? ""
        color = data["color"]?.string ?? "#5B8DEF"
        let minutes = data["targetMinutes"]?.number ?? 30
        targetMinutes = minutes.isFinite && minutes >= 0 ? Int(minutes) : 30
        recurrence = data["recurrence"]?.string ?? "daily"
        weekdays = (data["weekdays"]?.array ?? []).compactMap { $0.string }
        dayMinutes = (data["dayMinutes"]?.object ?? [:]).compactMapValues { $0.number.map { Int($0) } }
        lockMode = LockMode(rawValue: data["lockMode"]?.string ?? "soft") ?? .soft
        domainWhitelist = (data["domainWhitelist"]?.array ?? []).compactMap { $0.string }
        archived = data["archived"]?.bool ?? false
        updatedAt = WireTime.parseWire(record.updatedAt)
        deviceId = record.deviceId
        isDeleted = record.isDeleted
        extra = data.filter { !Self.known.contains($0.key) }
    }

    /// Minutes asked for on `date`, or nil when the task has no daily goal that day.
    public func targetMinutes(on date: Date, calendar: Calendar = .current) -> Int? {
        SharedStore.CachedTask(self).goalMinutes(on: date, calendar: calendar)
    }
}

/// One finished focus session, in the shape session_history.py files it.
public struct HistoryEntry: Identifiable, Equatable {
    public var id: String
    public var start: Date
    public var end: Date
    public var endType: String
    public var lockMode: LockMode
    public var source: String
    public var eventId: String?
    public var title: String?
    public var violationCount: Int
    public var beside: Bool

    public init(id: String, start: Date, end: Date, endType: String, lockMode: LockMode, source: String,
                eventId: String?, title: String?, violationCount: Int = 0, beside: Bool = false) {
        self.id = id; self.start = start; self.end = end; self.endType = endType; self.lockMode = lockMode
        self.source = source; self.eventId = eventId; self.title = title
        self.violationCount = violationCount; self.beside = beside
    }

    public var seconds: TimeInterval { max(0, end.timeIntervalSince(start)) }

    public init?(record: SyncRecord) {
        guard !record.isDeleted, let data = record.data.object,
              let startText = data["startTime"]?.string, let endText = data["endTime"]?.string,
              let start = WireTime.parseWire(startText), let end = WireTime.parseWire(endText) else { return nil }
        self.init(id: record.syncId, start: start, end: end,
                  endType: data["endType"]?.string ?? "natural",
                  lockMode: LockMode(rawValue: data["lockMode"]?.string ?? "soft") ?? .soft,
                  source: data["source"]?.string ?? "manual",
                  eventId: data["eventId"]?.string, title: data["eventTitle"]?.string,
                  violationCount: Int(data["violationCount"]?.number ?? 0),
                  beside: data["beside"]?.bool ?? false)
    }

    /// The record the desktop reads back: the same keys session_manager files, times as UTC with a zone.
    public func record(deviceId: String) -> SyncRecord {
        var data: [String: JSONValue] = [
            "startTime": .string(WireTime.wireString(start)),
            "endTime": .string(WireTime.wireString(end)),
            "endType": .string(endType),
            "reason": .null,
            "lockMode": .string(lockMode.rawValue),
            "processBlocklist": .array([]),
            "domainWhitelist": .array([]),
            "violationCount": .number(Double(violationCount)),
            "violationLog": .array([]),
            "domainWhitelistAdditions": .array([]),
            "processBlocklistExceptions": .array([]),
            "source": .string(source),
            "reviewProblemName": .null,
            "reviewSubjectName": .null,
            "pomodoro": .null,
            "sessionId": .string(id),
            "isBurnout": .bool(false),
        ]
        data["eventId"] = eventId.map { .string($0) } ?? .null
        data["eventTitle"] = title.map { .string($0) } ?? .null
        return SyncRecord(table: "session_history", syncId: id, data: .object(data), deviceId: deviceId,
                          updatedAt: WireTime.wireString(end), isDeleted: false)
    }
}

/// Day totals the Home and Tasks screens show.
public enum Progress {
    public static func seconds(on day: Date, taskId: String? = nil, in history: [HistoryEntry],
                               calendar: Calendar = .current) -> TimeInterval {
        history.filter { !$0.beside && (taskId == nil || $0.eventId == taskId) && calendar.isDate($0.start, inSameDayAs: day) }
            .reduce(0) { $0 + $1.seconds }
    }

    /// Days in a row with some focus, counted back from today (or from yesterday while today is still empty).
    public static func streak(history: [HistoryEntry], today: Date = Date(), calendar: Calendar = .current) -> Int {
        var day = calendar.startOfDay(for: today)
        if seconds(on: day, in: history, calendar: calendar) < 60 {
            day = calendar.date(byAdding: .day, value: -1, to: day)!
        }
        var run = 0
        while seconds(on: day, in: history, calendar: calendar) >= 60 {
            run += 1
            day = calendar.date(byAdding: .day, value: -1, to: day)!
        }
        return run
    }
}
