import XCTest
@testable import CarmenCore

final class WireTimeTests: XCTestCase {
    func testLocalAndWireTimesAgree() {
        let zone = TimeZone(identifier: "America/Los_Angeles")!
        let local = WireTime.parseLocal("2026-11-11T13:33:53.250000", zone: zone)!
        XCTAssertEqual(WireTime.wireString(local), "2026-11-11T21:33:53.250000+00:00")
        XCTAssertEqual(WireTime.localString(local, zone: zone), "2026-11-11T13:33:53.250000")
    }

    func testParsesWhatPythonWrites() {
        XCTAssertNotNil(WireTime.parseWire("2026-11-11T21:33:53.123456+00:00"))
        XCTAssertNotNil(WireTime.parseWire("2026-11-11T21:33:53+00:00"))
        XCTAssertNotNil(WireTime.parseWire("2026-11-11T21:33:53Z"))
        XCTAssertNil(WireTime.parseWire("not a time"))
    }

    func testWireStringRoundTrips() {
        let date = Date(timeIntervalSince1970: 1_800_000_000.5)
        XCTAssertEqual(WireTime.parseWire(WireTime.wireString(date))!.timeIntervalSince1970, date.timeIntervalSince1970, accuracy: 0.01)
    }
}

final class SessionEngineTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testCountsDown() {
        let s = SessionState(start: t0, plannedSeconds: 1500, lockMode: .soft)
        XCTAssertEqual(s.remainingSeconds(at: t0.addingTimeInterval(300)), 1200, accuracy: 0.01)
        XCTAssertFalse(s.isFinished(at: t0.addingTimeInterval(1499)))
        XCTAssertTrue(s.isFinished(at: t0.addingTimeInterval(1500)))
    }

    func testPausedTimeDoesNotCount() {
        var s = SessionState(start: t0, plannedSeconds: 1500, lockMode: .soft)
        XCTAssertEqual(s.pause(reason: "Break", at: t0.addingTimeInterval(600)), .paused)
        XCTAssertEqual(s.remainingSeconds(at: t0.addingTimeInterval(900)), 900, accuracy: 0.01)
        s.resume(at: t0.addingTimeInterval(900))
        XCTAssertEqual(s.remainingSeconds(at: t0.addingTimeInterval(1000)), 800, accuracy: 0.01)
        XCTAssertFalse(s.isFinished(at: t0.addingTimeInterval(1500)))
        XCTAssertTrue(s.isFinished(at: t0.addingTimeInterval(1800)))
    }

    func testHardLockPauseBudgetRunsOut() {
        var s = SessionState(start: t0, plannedSeconds: 3600, lockMode: .hard)
        XCTAssertEqual(s.pause(reason: "Call", at: t0.addingTimeInterval(60)), .paused)
        XCTAssertEqual(s.pause(reason: "Again", at: t0.addingTimeInterval(61)), .alreadyPaused)
        s.settleBudget(at: t0.addingTimeInterval(60 + 700))
        XCTAssertFalse(s.isPaused())
        XCTAssertEqual(s.pausedSeconds(at: t0.addingTimeInterval(2000)), SessionState.hardLockPauseBudget, accuracy: 0.01)
        XCTAssertEqual(s.pause(reason: "More", at: t0.addingTimeInterval(2000)), .budgetSpent)
    }

    func testSoftLockHasNoBudget() {
        var s = SessionState(start: t0, plannedSeconds: 3600, lockMode: .soft)
        XCTAssertNil(s.pauseBudgetLeft(at: t0))
        s.pause(reason: "Long", at: t0.addingTimeInterval(10))
        s.settleBudget(at: t0.addingTimeInterval(5000))
        XCTAssertTrue(s.isPaused())
    }

    func testNaturalEndIsFiledAtThePlannedTime() {
        var s = SessionState(start: t0, plannedSeconds: 1500, lockMode: .soft, taskId: "abc", title: "Essay")
        s.pause(reason: "Break", at: t0.addingTimeInterval(300)); s.resume(at: t0.addingTimeInterval(360))
        let entry = s.historyEntry(endingAt: t0.addingTimeInterval(5000), endType: "natural")
        XCTAssertEqual(entry.seconds, 1560, accuracy: 0.01)          // 25 min of focus plus the minute paused
        XCTAssertEqual(entry.source, "task")
        XCTAssertEqual(entry.eventId, "abc")
    }

    func testMinimumLengthIsOneMinute() {
        XCTAssertEqual(SessionState(start: t0, plannedSeconds: 5, lockMode: .soft).plannedSeconds, 60)
    }
}

final class RecordTests: XCTestCase {
    private func record(_ json: String, table: String = "tasks", id: String = "t1") throws -> SyncRecord {
        let data = try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
        return SyncRecord(table: table, syncId: id, data: data, deviceId: "pc", updatedAt: "2026-11-11T21:00:00+00:00", isDeleted: false)
    }

    func testTaskDecodesWhatTheDesktopWritesAndKeepsTheRest() throws {
        let task = CarmenTask(record: try record("""
        {"name":"Essay","color":"#E53935","targetMinutes":45,"recurrence":"weekly_days","weekdays":["MO","WE"],
         "dayMinutes":{"WE":90},"lockMode":"hard","domainWhitelist":["docs.google.com"],"hideTaskbarBadges":true}
        """))
        XCTAssertEqual(task.name, "Essay")
        XCTAssertEqual(task.lockMode, .hard)
        XCTAssertEqual(task.dayMinutes["WE"], 90)
        XCTAssertEqual(task.data["hideTaskbarBadges"], .bool(true))
    }

    func testMalformedTaskFallsBackToDefaults() throws {
        let task = CarmenTask(record: try record(#"{"name":7,"targetMinutes":"x","weekdays":"no","lockMode":"weird"}"#))
        XCTAssertEqual(task.name, "")
        XCTAssertEqual(task.targetMinutes, 30)
        XCTAssertEqual(task.lockMode, .soft)
        XCTAssertEqual(task.weekdays, [])
    }

    func testWeeklyGoalOnlyAppliesOnItsDays() throws {
        let task = CarmenTask(record: try record(#"{"name":"A","targetMinutes":30,"recurrence":"weekly_days","weekdays":["MO"],"dayMinutes":{"MO":50}}"#))
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "UTC")!
        let monday = calendar.date(from: DateComponents(year: 2026, month: 11, day: 9))!
        XCTAssertEqual(task.goalMinutes(on: monday, calendar: calendar), 50)
        XCTAssertNil(task.goalMinutes(on: monday.addingTimeInterval(86400), calendar: calendar))
    }

    func testHistoryRoundTripsThroughTheDesktopShape() throws {
        let start = Date(timeIntervalSince1970: 1_800_000_000)
        let entry = HistoryEntry(id: "s1", start: start, end: start.addingTimeInterval(1500), endType: "natural",
                                 lockMode: .hard, source: "task", eventId: "t1", title: "Essay")
        let record = entry.record(deviceId: "ios-1")
        XCTAssertEqual(record.table, "session_history")
        let back = HistoryEntry(record: record)!
        XCTAssertEqual(back.seconds, 1500, accuracy: 0.01)
        XCTAssertEqual(back.lockMode, .hard)
        let keys = Set(record.data.object!.keys)
        XCTAssertTrue(keys.isSuperset(of: ["startTime", "endTime", "endType", "violationLog", "sessionId", "eventTitle"]))
    }

    func testStreakCountsBackAndSkipsAnEmptyToday() {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "UTC")!
        let today = calendar.date(from: DateComponents(year: 2026, month: 11, day: 11, hour: 12))!
        func day(_ back: Int) -> HistoryEntry {
            let start = calendar.date(byAdding: .day, value: -back, to: today)!
            return HistoryEntry(id: "d\(back)", start: start, end: start.addingTimeInterval(1800), endType: "natural",
                                lockMode: .soft, source: "manual", eventId: nil, title: nil)
        }
        XCTAssertEqual(Progress.streak(history: [day(1), day(2), day(3)], today: today, calendar: calendar), 3)
        XCTAssertEqual(Progress.streak(history: [day(0), day(1), day(3)], today: today, calendar: calendar), 2)
        XCTAssertEqual(Progress.streak(history: [], today: today, calendar: calendar), 0)
    }
}

final class TaskEditTests: XCTestCase {
    func testNewTaskCarriesEveryFieldTheDesktopExpects() {
        let task = CarmenTask(name: "Essay", color: "#E53935", targetMinutes: 45, lockMode: .hard, deviceId: "ios-1")
        for key in ["name", "color", "targetMinutes", "recurrence", "weekdays", "dayMinutes", "lockMode", "processBlocklist",
                    "domainWhitelist", "cashedInDates", "targetMinutesHistory", "archived"] {
            XCTAssertNotNil(task.data[key], key)
        }
        XCTAssertEqual(task.id.count, 32)
        XCTAssertFalse(task.id.contains("-"))
        let record = task.record(deviceId: "ios-1")
        XCTAssertEqual(record.table, "tasks")
        XCTAssertNil(record.data.object?["id"])
    }

    func testEditingKeepsFieldsThePhoneDoesNotKnow() throws {
        let raw = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"name":"A","targetMinutes":30,"stopTaskbarFlashing":true,"targetMinutesHistory":[]}"#.utf8))
        var task = CarmenTask(record: SyncRecord(table: "tasks", syncId: "t", data: raw, deviceId: "pc",
                                                  updatedAt: "2026-11-11T21:00:00+00:00", isDeleted: false))
        task.name = "B"
        task.setTarget(50)
        XCTAssertEqual(task.data["stopTaskbarFlashing"], .bool(true))
        XCTAssertEqual(task.targetMinutes, 50)
        XCTAssertEqual(task.data["targetMinutesHistory"]?.array?.count, 1)
    }

    func testTombstoneIsMarkedDeleted() {
        let task = CarmenTask(name: "X", color: "#5B8DEF", targetMinutes: 30, lockMode: .soft, deviceId: "ios-1")
        XCTAssertTrue(task.record(deviceId: "ios-1", deleted: true).isDeleted)
    }
}

final class StoreTests: XCTestCase {
    func testOutboxNeverFilesTheSameSessionTwice() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = SharedStore(directory: dir)
        let s = SessionState(start: Date(), plannedSeconds: 600, lockMode: .soft)
        let entry = s.historyEntry(endingAt: Date().addingTimeInterval(700), endType: "natural")
        store.file(entry); store.file(entry)
        XCTAssertEqual(store.outbox.count, 1)
        store.clearOutbox(sent: [entry.id])
        XCTAssertTrue(store.outbox.isEmpty)
    }

    func testDeviceIdIsStable() {
        let store = SharedStore(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        XCTAssertEqual(store.deviceId, store.deviceId)
        XCTAssertTrue(store.deviceId.hasPrefix("ios-"))
    }
}


final class PomodoroTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)
    let plan = PomodoroPlan(focusMinutes: 25, breakMinutes: 5, cycles: 3)

    func testPhasesFollowTheRunningTime() {
        XCTAssertEqual(plan.totalSeconds, 3 * 30 * 60)
        var p = plan.phase(afterRunning: 10 * 60)
        XCTAssertEqual(p.cycle, 1); XCTAssertFalse(p.isBreak); XCTAssertEqual(p.remaining, 15 * 60, accuracy: 0.01)
        p = plan.phase(afterRunning: 26 * 60)
        XCTAssertTrue(p.isBreak); XCTAssertEqual(p.remaining, 4 * 60, accuracy: 0.01)
        p = plan.phase(afterRunning: 31 * 60)
        XCTAssertEqual(p.cycle, 2); XCTAssertFalse(p.isBreak)
        p = plan.phase(afterRunning: 89 * 60)
        XCTAssertEqual(p.cycle, 3); XCTAssertTrue(p.isBreak)
    }

    func testEndsAfterTheLastBreak() {
        let s = SessionState(start: t0, plannedSeconds: 0, lockMode: .soft, pomodoro: plan)
        XCTAssertFalse(s.isFinished(at: t0.addingTimeInterval(89 * 60)))
        XCTAssertTrue(s.isFinished(at: t0.addingTimeInterval(90 * 60)))
    }

    func testShieldIsOffDuringABreakAndWhilePaused() {
        var s = SessionState(start: t0, plannedSeconds: 0, lockMode: .soft, pomodoro: plan)
        XCTAssertTrue(s.isEnforcing(at: t0.addingTimeInterval(60)))
        XCTAssertFalse(s.isEnforcing(at: t0.addingTimeInterval(26 * 60)))
        s.pause(reason: "break", at: t0.addingTimeInterval(120))
        XCTAssertFalse(s.isEnforcing(at: t0.addingTimeInterval(200)))
    }

    func testPausedTimeDoesNotAdvanceThePhase() {
        var s = SessionState(start: t0, plannedSeconds: 0, lockMode: .soft, pomodoro: plan)
        s.pause(reason: "break", at: t0.addingTimeInterval(20 * 60))
        s.resume(at: t0.addingTimeInterval(50 * 60))
        XCTAssertFalse(s.phase(at: t0.addingTimeInterval(54 * 60))!.isBreak)      // 24 minutes of running time
        XCTAssertTrue(s.phase(at: t0.addingTimeInterval(56 * 60))!.isBreak)
    }

    func testFocusSpansForScheduling() {
        let spans = plan.focusSpans()
        XCTAssertEqual(spans.count, 3)
        XCTAssertEqual(spans[1].start, 30 * 60); XCTAssertEqual(spans[1].end, 55 * 60)
    }
}

final class LiveWireTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testARunningSessionIsWrittenInTheDesktopsShape() {
        var s = SessionState(id: "abc", start: t0, plannedSeconds: 1500, lockMode: .hard, taskId: "t1", title: "Essay",
                             allowedSites: ["docs.google.com"])
        s.pause(reason: "break", at: t0.addingTimeInterval(300))
        let record = LiveWire.record(session: s, endedSessionId: nil, deviceId: "ios-1", now: t0.addingTimeInterval(400))
        XCTAssertEqual(record.table, "live_session"); XCTAssertEqual(record.syncId, "current")
        let d = record.data.object!
        XCTAssertEqual(d["isActive"], .bool(true))
        XCTAssertEqual(d["sessionId"], .string("abc"))
        XCTAssertEqual(d["isPaused"], .bool(true))
        XCTAssertEqual(d["frozenSecondsRemaining"], .number(1200))
        XCTAssertEqual(d["eventId"], .string("t1"))
        XCTAssertEqual(d["domainWhitelist"], .array([.string("docs.google.com")]))
        XCTAssertEqual(d["violationLog"]?.array?.count, 1)
        XCTAssertEqual(d["violationLog"]?.array?.first?.object?["pauseReason"], .string("break"))
    }

    func testEndedIsMarkedWithTheIdThatEnded() {
        let record = LiveWire.record(session: nil, endedSessionId: "abc", deviceId: "ios-1", now: t0)
        XCTAssertEqual(record.data.object?["isActive"], .bool(false))
        XCTAssertEqual(record.data.object?["endedSessionId"], .string("abc"))
    }

    func testTimesAreWrittenInCloudTimeAndReadBack() {
        let s = SessionState(id: "abc", start: t0, plannedSeconds: 1500, lockMode: .soft)
        let record = LiveWire.record(session: s, endedSessionId: nil, deviceId: "ios-1", now: t0, offset: 90)
        guard case .running(let remote)? = LiveWire.parse(record, offset: 90) else { return XCTFail("not running") }
        XCTAssertEqual(remote.start.timeIntervalSince1970, t0.timeIntervalSince1970, accuracy: 0.01)
        XCTAssertEqual(remote.blockEnd.timeIntervalSince1970, t0.addingTimeInterval(1500).timeIntervalSince1970, accuracy: 0.01)
        XCTAssertEqual(remote.remaining(at: t0.addingTimeInterval(500)), 1000, accuracy: 0.01)
        XCTAssertTrue(remote.isEnforcing(at: t0.addingTimeInterval(500)))
    }

    func testAPomodoroSessionCarriesItsBlockAndBreakFlag() {
        let s = SessionState(id: "p", start: t0, plannedSeconds: 0, lockMode: .soft,
                             pomodoro: PomodoroPlan(focusMinutes: 25, breakMinutes: 5, cycles: 2))
        let during = t0.addingTimeInterval(26 * 60)
        let record = LiveWire.record(session: s, endedSessionId: nil, deviceId: "ios-1", now: during)
        guard case .running(let remote)? = LiveWire.parse(record) else { return XCTFail("not running") }
        XCTAssertTrue(remote.isBreak)
        XCTAssertFalse(remote.isEnforcing(at: during))
        XCTAssertEqual(record.data.object?["pomodoro"]?.object?["currentCycle"], .number(1))
    }

    func testReadsADesktopSessionAndAnIdleRecord() throws {
        let running = try JSONDecoder().decode(JSONValue.self, from: Data("""
        {"isActive":true,"sessionId":"d1","startTime":"2026-11-11T21:00:00.000000+00:00",
         "endTime":"2026-11-11T21:25:00.000000+00:00","lockMode":"hard","isPaused":false,"eventTitle":"Lab","domainWhitelist":["a.com"]}
        """.utf8))
        let record = SyncRecord(table: "live_session", syncId: "current", data: running, deviceId: "pc",
                                updatedAt: "2026-11-11T21:00:01+00:00", isDeleted: false)
        guard case .running(let r)? = LiveWire.parse(record) else { return XCTFail("not running") }
        XCTAssertEqual(r.title, "Lab"); XCTAssertEqual(r.lockMode, .hard); XCTAssertEqual(r.domainWhitelist, ["a.com"])
        let idle = SyncRecord(table: "live_session", syncId: "current",
                              data: .object(["isActive": .bool(false), "endedSessionId": .string("d1")]),
                              deviceId: "pc", updatedAt: "2026-11-11T21:26:00+00:00", isDeleted: false)
        XCTAssertEqual(LiveWire.parse(idle), .idle(endedSessionId: "d1", stamp: "2026-11-11T21:26:00+00:00"))
    }

    func testServerClockLearnsTheGap() {
        let clock = ServerClock()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        clock.note(dateHeader: "Fri, 15 Jan 2027 08:00:00 GMT", now: Date(timeIntervalSince1970: 1_799_999_990))
        XCTAssertTrue(abs(clock.offset) < 7 * 24 * 3600)
        _ = now
    }
}
