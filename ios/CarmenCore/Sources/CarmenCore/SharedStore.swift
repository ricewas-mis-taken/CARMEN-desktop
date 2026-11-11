import Foundation

/// Small files in the App Group container, shared by the app and its Screen Time extensions.
/// The extensions only ever need the running session and the list of finished ones waiting to be sent.
public final class SharedStore {
    public static let groupId = "group.app.carmenfocus"

    private let directory: URL
    private let queue = DispatchQueue(label: "carmen.sharedstore")

    public init(directory: URL? = nil) {
        if let directory {
            self.directory = directory
        } else if let group = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: Self.groupId) {
            self.directory = group
        } else {
            self.directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        }
        try? FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }

    private func url(_ name: String) -> URL { directory.appendingPathComponent(name) }

    private func read<T: Decodable>(_ name: String, as type: T.Type) -> T? {
        queue.sync { () -> T? in
            guard let data = try? Data(contentsOf: url(name)) else { return nil }
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            return try? decoder.decode(T.self, from: data)
        }
    }

    private func write<T: Encodable>(_ value: T?, _ name: String) {
        queue.sync {
            guard let value else { try? FileManager.default.removeItem(at: url(name)); return }
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            if let data = try? encoder.encode(value) { try? data.write(to: url(name), options: .atomic) }
        }
    }

    // MARK: running session

    public var session: SessionState? {
        get { read("session.json", as: SessionState.self) }
        set { write(newValue, "session.json") }
    }

    // MARK: finished sessions not yet sent to the cloud

    public struct Outbox: Codable { public var entries: [Entry] }
    public struct Entry: Codable, Equatable {
        public var id: String, start: Date, end: Date, endType: String, lockMode: LockMode
        public var taskId: String?, title: String?, violations: Int
        public var pomodoro: PomodoroPlan?
        public init(_ e: HistoryEntry) {
            id = e.id; start = e.start; end = e.end; endType = e.endType; lockMode = e.lockMode
            taskId = e.eventId; title = e.title; violations = e.violationCount; pomodoro = e.pomodoro
        }
        public var history: HistoryEntry {
            HistoryEntry(id: id, start: start, end: end, endType: endType, lockMode: lockMode,
                         source: taskId == nil ? "manual" : "task", eventId: taskId, title: title, violationCount: violations,
                         pomodoro: pomodoro)
        }
    }

    public var outbox: [HistoryEntry] {
        (read("outbox.json", as: Outbox.self)?.entries ?? []).map(\.history)
    }

    /// Adds a finished session once; the same id is never filed twice (an extension and the app can both end it).
    public func file(_ entry: HistoryEntry) {
        var entries = read("outbox.json", as: Outbox.self)?.entries ?? []
        guard !entries.contains(where: { $0.id == entry.id }) else { return }
        entries.append(Entry(entry))
        write(Outbox(entries: entries), "outbox.json")
    }

    public func clearOutbox(sent ids: Set<String>) {
        let kept = (read("outbox.json", as: Outbox.self)?.entries ?? []).filter { !ids.contains($0.id) }
        write(Outbox(entries: kept), "outbox.json")
    }

    // MARK: cache of what the cloud last said

    public struct Cache: Codable {
        public var tasks: [CarmenTask] = []
        public var history: [Entry] = []
        public var cursor: String?
        public var live: JSONValue?
        public var liveStamp: String?
    }

    /// Task edits made here that the cloud has not seen yet.
    public var taskOutbox: [CarmenTask] {
        get { read("task-outbox.json", as: [CarmenTask].self) ?? [] }
        set { write(newValue.isEmpty ? nil : newValue, "task-outbox.json") }
    }

    public var cache: Cache {
        get { read("cache.json", as: Cache.self) ?? Cache() }
        set { write(newValue, "cache.json") }
    }

    /// Where this phone's pulls resume from, and the id that marks records as coming from this phone.
    public var deviceId: String {
        if let existing = read("device.json", as: [String: String].self)?["id"] { return existing }
        let id = "ios-" + UUID().uuidString.lowercased()
        write(["id": id], "device.json")
        return id
    }
}
