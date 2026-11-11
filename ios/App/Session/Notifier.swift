import Foundation
import UserNotifications
import CarmenCore

/// Tells the person a block or a break is over while Carmen is closed.
enum Notifier {
    private static let prefix = "carmen.phase."

    static func ask() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    static func cancel() {
        let center = UNUserNotificationCenter.current()
        center.getPendingNotificationRequests { requests in
            center.removePendingNotificationRequests(withIdentifiers: requests.map(\.identifier).filter { $0.hasPrefix(prefix) })
        }
    }

    /// One notice per coming boundary: break time, back to focus, and the end.
    static func schedule(for session: SessionState, now: Date = Date()) {
        cancel()
        guard !session.isPaused() else { return }
        let center = UNUserNotificationCenter.current()
        var moments: [(Date, String, String)] = []
        let running = session.focusedSeconds(at: now)
        if let plan = session.pomodoro {
            let focus = TimeInterval(plan.focusMinutes * 60), round = TimeInterval((plan.focusMinutes + plan.breakMinutes) * 60)
            for cycle in 0..<plan.cycles {
                let breakStart = TimeInterval(cycle) * round + focus
                let next = TimeInterval(cycle + 1) * round
                if breakStart > running {
                    moments.append((now.addingTimeInterval(breakStart - running), "Break time", "Cycle \(cycle + 1) of \(plan.cycles) done."))
                }
                if next > running && cycle + 1 < plan.cycles {
                    moments.append((now.addingTimeInterval(next - running), "Back to focus", "Starting cycle \(cycle + 2) of \(plan.cycles)."))
                }
            }
        }
        moments.append((session.projectedEnd(at: now), "Session complete", session.title ?? "Nice work."))
        for (index, moment) in moments.enumerated() where moment.0.timeIntervalSince(now) > 1 {
            let content = UNMutableNotificationContent()
            content.title = moment.1
            content.body = moment.2
            content.sound = .default
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: moment.0.timeIntervalSince(now), repeats: false)
            center.add(UNNotificationRequest(identifier: prefix + String(index), content: content, trigger: trigger))
        }
    }
}
