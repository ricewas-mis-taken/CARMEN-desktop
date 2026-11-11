import Foundation

/// This phone's clock against the cloud's, learned from the Date header of every answer (the same job
/// server_clock.py does on the desktop). Times in the shared running session are written in the cloud's time,
/// so a computer with a wrong clock still agrees on when a session ends.
public final class ServerClock {
    public static let shared = ServerClock()
    public private(set) var offset: TimeInterval = 0
    private let lock = NSLock()

    public init() {}

    public func set(offset value: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        offset = value
    }

    /// `header` is an HTTP date like "Wed, 11 Nov 2026 21:33:53 GMT". The header has whole seconds, rounded down.
    public func note(dateHeader header: String?, now: Date = Date()) {
        guard let header else { return }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "GMT")
        f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        guard let server = f.date(from: header) else { return }
        let gap = server.timeIntervalSince1970 + 0.5 - now.timeIntervalSince1970
        if abs(gap) <= 7 * 24 * 3600 { set(offset: gap) }
    }
}

/// The running session in the shape the desktop shares it (session_manager.export_live_state), one row per
/// account: table `live_session`, sync id `current`. Only the fields a phone session means are written; the
/// desktop fills in the rest with its defaults.
public enum LiveWire {
    public static let table = "live_session"
    public static let syncId = "current"

    static func cloudTime(_ date: Date, offset: TimeInterval) -> JSONValue {
        .string(WireTime.wireString(date.addingTimeInterval(offset)))
    }

    static func localTime(_ value: JSONValue?, offset: TimeInterval) -> Date? {
        guard let text = value?.string, let date = WireTime.parseWire(text) else { return nil }
        return date.addingTimeInterval(-offset)
    }

    public struct Reason: Equatable {
        public let code: String, title: String
        public static let all = [
            Reason(code: "break", title: "A break"), Reason(code: "interrupted", title: "Something came up"),
            Reason(code: "stuck", title: "I am stuck"), Reason(code: "checking", title: "Checking something"),
        ]
    }

    /// The record that says what is running now, or (session nil) that `endedSessionId` just ended.
    public static func record(session: SessionState?, endedSessionId: String?, deviceId: String, now: Date = Date(),
                              offset: TimeInterval = 0) -> SyncRecord {
        var data: [String: JSONValue] = ["isActive": .bool(false), "parkedSessions": .array([])]
        if let session {
            let phase = session.phase(at: now)
            let paused = session.isPaused()
            var log: [JSONValue] = []
            for pause in session.pauses {
                var marker: [String: JSONValue] = ["kind": .string("pause"), "timestamp": cloudTime(pause.start, offset: offset),
                                                    "pauseReason": .string(pause.reason)]
                if pause.reason == "later" { marker["pauseReason"] = .string("break") }
                log.append(.object(marker))
                if let end = pause.end { log.append(.object(["kind": .string("resume"), "timestamp": cloudTime(end, offset: offset)])) }
            }
            let remaining = session.remainingSeconds(at: now)
            let blockLeft = phase.map { $0.remaining } ?? remaining
            data["isActive"] = .bool(true)
            data["startTime"] = cloudTime(session.start, offset: offset)
            data["endTime"] = cloudTime(now.addingTimeInterval(blockLeft), offset: offset)
            data["lockMode"] = .string(session.lockMode.rawValue)
            data["processBlocklist"] = .array([])
            data["domainWhitelist"] = .array(session.allowedSites.map { .string($0) })
            data["violationCount"] = .number(Double(session.violations))
            data["violationLog"] = .array(log)
            data["isPaused"] = .bool(paused)
            data["pausedAt"] = paused ? (session.pauses.last.map { cloudTime($0.start, offset: offset) } ?? .null) : .null
            data["frozenSecondsRemaining"] = paused ? .number(blockLeft.rounded(.down)) : .null
            data["source"] = .string(session.taskId == nil ? "manual" : "task")
            data["eventId"] = session.taskId.map { .string($0) } ?? .null
            data["eventTitle"] = session.title.map { .string($0) } ?? .null
            data["sessionId"] = .string(session.id)
            data["isBurnout"] = .bool(false)
            data["isBreak"] = .bool(phase?.isBreak ?? false)
            if let plan = session.pomodoro, let phase {
                data["pomodoro"] = .object([
                    "focusMinutes": .number(Double(plan.focusMinutes)), "breakMinutes": .number(Double(plan.breakMinutes)),
                    "totalCycles": .number(Double(plan.cycles)), "currentCycle": .number(Double(phase.cycle)),
                    "phase": .string(phase.isBreak ? "break" : "focus"),
                ])
            }
        } else if let endedSessionId {
            data["endedSessionId"] = .string(endedSessionId)
        }
        return SyncRecord(table: table, syncId: syncId, data: .object(data), deviceId: deviceId,
                          updatedAt: WireTime.wireString(now.addingTimeInterval(offset)), isDeleted: false)
    }

    /// A session running somewhere else, as the phone shows it.
    public struct Remote: Equatable {
        public var sessionId: String
        public var title: String?
        public var taskId: String?
        public var lockMode: LockMode
        public var start: Date
        public var blockEnd: Date          // end of the current block (the focus block or the break)
        public var isPaused: Bool
        public var frozenSeconds: Double?
        public var isBreak: Bool
        public var domainWhitelist: [String]
        public var deviceId: String
        public var stamp: String

        public func remaining(at now: Date) -> TimeInterval {
            if isPaused, let frozenSeconds { return frozenSeconds }
            return max(0, blockEnd.timeIntervalSince(now))
        }

        /// Whether the phone should be shielding for it right now.
        public func isEnforcing(at now: Date) -> Bool { !isPaused && !isBreak && remaining(at: now) > 0 }
    }

    public enum Parsed: Equatable {
        case running(Remote)
        case idle(endedSessionId: String?, stamp: String)
    }

    public static func parse(_ record: SyncRecord, offset: TimeInterval = 0) -> Parsed? {
        guard record.table == table, record.syncId == syncId, let data = record.data.object else { return nil }
        guard data["isActive"]?.bool == true else {
            return .idle(endedSessionId: data["endedSessionId"]?.string, stamp: record.updatedAt)
        }
        guard let id = data["sessionId"]?.string, let start = localTime(data["startTime"], offset: offset),
              let end = localTime(data["endTime"], offset: offset) else { return nil }
        return .running(Remote(
            sessionId: id, title: data["eventTitle"]?.string, taskId: data["eventId"]?.string,
            lockMode: LockMode(rawValue: data["lockMode"]?.string ?? "soft") ?? .soft,
            start: start, blockEnd: end, isPaused: data["isPaused"]?.bool ?? false,
            frozenSeconds: data["frozenSecondsRemaining"]?.number, isBreak: data["isBreak"]?.bool ?? false,
            domainWhitelist: (data["domainWhitelist"]?.array ?? []).compactMap { $0.string },
            deviceId: record.deviceId, stamp: record.updatedAt))
    }
}
