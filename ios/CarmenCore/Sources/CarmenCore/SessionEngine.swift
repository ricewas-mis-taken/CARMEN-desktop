import Foundation

/// `cycles` rounds of a focus block then a break, the way the desktop's Pomodoro sessions run. The last break ends it.
public struct PomodoroPlan: Codable, Equatable {
    public var focusMinutes: Int
    public var breakMinutes: Int
    public var cycles: Int

    public init(focusMinutes: Int, breakMinutes: Int, cycles: Int) {
        self.focusMinutes = max(1, focusMinutes); self.breakMinutes = max(0, breakMinutes); self.cycles = max(1, cycles)
    }

    public var totalSeconds: Int { cycles * (focusMinutes + breakMinutes) * 60 }

    public struct Phase: Equatable {
        public var cycle: Int            // 1-based
        public var isBreak: Bool
        public var remaining: TimeInterval
        public var start: TimeInterval   // seconds of running time at which this phase began
        public var length: TimeInterval
    }

    /// Where `running` seconds of un-paused time puts us.
    public func phase(afterRunning running: TimeInterval) -> Phase {
        let focus = TimeInterval(focusMinutes * 60), rest = TimeInterval(breakMinutes * 60)
        let round = focus + rest
        let clamped = max(0, min(running, TimeInterval(totalSeconds) - 0.001))
        let cycle = Int(clamped / round)
        let within = clamped - TimeInterval(cycle) * round
        if within < focus {
            return Phase(cycle: cycle + 1, isBreak: false, remaining: focus - within,
                         start: TimeInterval(cycle) * round, length: focus)
        }
        return Phase(cycle: cycle + 1, isBreak: true, remaining: round - within,
                     start: TimeInterval(cycle) * round + focus, length: rest)
    }

    /// Running-time spans of every focus block, for scheduling the system's own shield on and off.
    public func focusSpans() -> [(start: TimeInterval, end: TimeInterval)] {
        let focus = TimeInterval(focusMinutes * 60), round = TimeInterval((focusMinutes + breakMinutes) * 60)
        return (0..<cycles).map { (start: TimeInterval($0) * round, end: TimeInterval($0) * round + focus) }
    }
}

/// A running focus session. Everything about it is a function of this value and the clock, so the app, the
/// shield extensions and the tests all agree on what is left without sharing a timer.
public struct SessionState: Codable, Equatable {
    public struct Pause: Codable, Equatable {
        public var start: Date
        public var end: Date?
        public var reason: String
    }

    public var id: String
    public var start: Date
    public var plannedSeconds: Int
    public var lockMode: LockMode
    public var taskId: String?
    public var title: String?
    public var pauses: [Pause] = []
    public var violations: Int = 0
    /// Apps the person chose to continue into during a soft lock; they stay open until the session ends.
    public var releasedApps: Int = 0
    /// Allow-only websites for this session; every other site is shielded when the list has anything in it.
    public var allowedSites: [String] = []
    public var pomodoro: PomodoroPlan?

    public static let hardLockPauseBudget: TimeInterval = 10 * 60

    public init(id: String = UUID().uuidString.lowercased(), start: Date, plannedSeconds: Int, lockMode: LockMode,
                taskId: String? = nil, title: String? = nil, allowedSites: [String] = [], pomodoro: PomodoroPlan? = nil) {
        self.id = id; self.start = start
        self.pomodoro = pomodoro
        self.plannedSeconds = pomodoro?.totalSeconds ?? max(60, plannedSeconds)
        self.lockMode = lockMode; self.taskId = taskId; self.title = title; self.allowedSites = allowedSites
    }

    /// The Pomodoro phase right now, or nil for a plain session.
    public func phase(at now: Date) -> PomodoroPlan.Phase? {
        pomodoro?.phase(afterRunning: focusedSeconds(at: now))
    }

    /// Blocking is off while paused and during a break.
    public func isEnforcing(at now: Date) -> Bool {
        !isPaused() && !(phase(at: now)?.isBreak ?? false)
    }

    public func isPaused() -> Bool { pauses.last.map { $0.end == nil } ?? false }

    public func pausedSeconds(at now: Date) -> TimeInterval {
        pauses.reduce(0) { $0 + max(0, ($1.end ?? now).timeIntervalSince($1.start)) }
    }

    /// Time that counted towards the session so far.
    public func focusedSeconds(at now: Date) -> TimeInterval {
        max(0, now.timeIntervalSince(start) - pausedSeconds(at: now))
    }

    public func remainingSeconds(at now: Date) -> TimeInterval {
        max(0, TimeInterval(plannedSeconds) - focusedSeconds(at: now))
    }

    public func isFinished(at now: Date) -> Bool { !isPaused() && remainingSeconds(at: now) <= 0 }

    /// When the session ends if nothing else is paused.
    public func projectedEnd(at now: Date) -> Date { now.addingTimeInterval(remainingSeconds(at: now)) }

    public func pauseBudgetLeft(at now: Date) -> TimeInterval? {
        lockMode == .hard ? max(0, Self.hardLockPauseBudget - pausedSeconds(at: now)) : nil
    }

    public enum PauseResult: Equatable { case paused, alreadyPaused, budgetSpent }

    @discardableResult
    public mutating func pause(reason: String, at now: Date) -> PauseResult {
        if isPaused() { return .alreadyPaused }
        if let left = pauseBudgetLeft(at: now), left <= 0 { return .budgetSpent }
        pauses.append(Pause(start: now, end: nil, reason: reason))
        return .paused
    }

    public mutating func resume(at now: Date) {
        guard isPaused() else { return }
        pauses[pauses.count - 1].end = now
    }

    /// While paused in a hard lock the budget runs down; when it is gone the pause ends by itself.
    public mutating func settleBudget(at now: Date) {
        guard isPaused(), let left = pauseBudgetLeft(at: now), left <= 0,
              let open = pauses.last, open.end == nil else { return }
        let spentBefore = pauses.dropLast().reduce(0) { $0 + ($1.end ?? now).timeIntervalSince($1.start) }
        pauses[pauses.count - 1].end = open.start.addingTimeInterval(Self.hardLockPauseBudget - spentBefore)
    }

    /// The history entry for ending now. `endType` is "natural", "manual" or "nuclear" like the desktop's.
    public func historyEntry(endingAt now: Date, endType: String) -> HistoryEntry {
        let end = endType == "natural" ? start.addingTimeInterval(TimeInterval(plannedSeconds) + pausedSeconds(at: now)) : now
        return HistoryEntry(id: id, start: start, end: min(end, now), endType: endType, lockMode: lockMode,
                            source: taskId == nil ? "manual" : "task", eventId: taskId, title: title,
                            violationCount: violations, pomodoro: pomodoro)
    }
}
