import Foundation

public enum LockMode: String, Codable, CaseIterable, Identifiable {
    case soft, hard
    public var id: String { rawValue }
    public var title: String { self == .soft ? "Soft lock" : "Hard lock" }
}

/// A task exactly as the desktop stores it (tasks_store.DEFAULT_TASK). The whole record is kept in `data`, so a
/// task edited here keeps every field the phone does not know about, and goes back to the desktop intact.
public struct CarmenTask: Identifiable, Equatable, Codable {
    public var id: String
    public var data: [String: JSONValue]
    public var updatedAt: Date?
    public var deviceId: String
    public var isDeleted: Bool

    public static let palette = ["#5B8DEF", "#E53935", "#43A047", "#FB8C00", "#8E24AA",
                                 "#00897B", "#3949AB", "#D81B60", "#6D4C41", "#546E7A"]
    /// Fields the wire record does not carry inside `data`.
    static let envelope: Set<String> = ["id", "updatedAt", "deviceId", "isDeleted"]

    public init(record: SyncRecord) {
        id = record.syncId
        data = (record.data.object ?? [:]).filter { !Self.envelope.contains($0.key) }
        updatedAt = WireTime.parseWire(record.updatedAt)
        deviceId = record.deviceId
        isDeleted = record.isDeleted
    }

    /// A new task with the desktop's defaults filled in, so the desktop never meets a missing field.
    public init(name: String, color: String, targetMinutes: Int, lockMode: LockMode, deviceId: String,
                now: Date = Date()) {
        id = UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
        let day = WireTime.localString(now).prefix(10)
        data = [
            "name": .string(name), "color": .string(color), "targetMinutes": .number(Double(targetMinutes)),
            "recurrence": .string("daily"), "weekdays": .array([]), "dayMinutes": .object([:]),
            "periodDays": .null, "periodAnchor": .null, "lockMode": .string(lockMode.rawValue),
            "processBlocklist": .array([]), "domainWhitelist": .array([]),
            "hideTaskbarBadges": .bool(false), "stopTaskbarFlashing": .bool(false),
            "cashedInDates": .object([:]),
            "targetMinutesHistory": .array([.object(["date": .string(String(day)), "minutes": .number(Double(targetMinutes))])]),
            "archived": .bool(false), "createdAt": .string(WireTime.localString(now)),
        ]
        updatedAt = now
        self.deviceId = deviceId
        isDeleted = false
    }

    public var name: String {
        get { data["name"]?.string ?? "" }
        set { data["name"] = .string(newValue) }
    }
    public var color: String {
        get { data["color"]?.string ?? "#5B8DEF" }
        set { data["color"] = .string(newValue) }
    }
    public var targetMinutes: Int {
        get {
            let minutes = data["targetMinutes"]?.number ?? 30
            return minutes.isFinite && minutes >= 0 && minutes < 100_000 ? Int(minutes) : 30
        }
        set { data["targetMinutes"] = .number(Double(max(0, newValue))) }
    }
    public var recurrence: String { data["recurrence"]?.string ?? "daily" }
    public var weekdays: [String] { (data["weekdays"]?.array ?? []).compactMap { $0.string } }
    public var dayMinutes: [String: Int] { (data["dayMinutes"]?.object ?? [:]).compactMapValues { $0.number.map { Int($0) } } }
    public var lockMode: LockMode {
        get { LockMode(rawValue: data["lockMode"]?.string ?? "soft") ?? .soft }
        set { data["lockMode"] = .string(newValue.rawValue) }
    }
    public var domainWhitelist: [String] {
        get { (data["domainWhitelist"]?.array ?? []).compactMap { $0.string } }
        set { data["domainWhitelist"] = .array(newValue.map { .string($0) }) }
    }
    public var processBlocklist: [String] { (data["processBlocklist"]?.array ?? []).compactMap { $0.string } }
    public var archived: Bool {
        get { data["archived"]?.bool ?? false }
        set { data["archived"] = .bool(newValue) }
    }

    /// Minutes asked for on `date`, or nil when there is no daily goal that day (period goals count as a total).
    public func goalMinutes(on date: Date, calendar: Calendar = .current) -> Int? {
        guard recurrence == "daily" || recurrence == "weekly_days" else { return nil }
        if recurrence == "daily" { return targetMinutes }
        let codes = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
        let code = codes[calendar.component(.weekday, from: date) - 1]
        guard weekdays.contains(code) else { return nil }
        return dayMinutes[code] ?? targetMinutes
    }

    /// Changing the daily target starts a new line of the target history, like tasks_store does.
    public mutating func setTarget(_ minutes: Int, on day: Date = Date()) {
        guard minutes != targetMinutes else { return }
        targetMinutes = minutes
        let today = String(WireTime.localString(day).prefix(10))
        var history = (data["targetMinutesHistory"]?.array ?? []).filter { $0.object?["date"]?.string != today }
        history.append(.object(["date": .string(today), "minutes": .number(Double(minutes))]))
        data["targetMinutesHistory"] = .array(history)
    }

    /// The record to push after an edit or delete here.
    public func record(deviceId: String, now: Date = Date(), deleted: Bool? = nil) -> SyncRecord {
        SyncRecord(table: "tasks", syncId: id, data: .object(data), deviceId: deviceId,
                   updatedAt: WireTime.wireString(now), isDeleted: deleted ?? isDeleted)
    }

    /// Whether this task should show on the phone at all.
    public var isShown: Bool { !isDeleted && !archived && !name.isEmpty }
}
