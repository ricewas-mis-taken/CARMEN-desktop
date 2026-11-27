import Foundation

public struct ReviewTopic: Codable, Equatable, Identifiable {
    public var id: String
    public var name: String
    public var order: Int
}

public struct ReviewSubject: Codable, Equatable, Identifiable {
    public var id: String
    public var name: String
    public var color: String
    public var topicId: String
}

public struct ReviewProblem: Codable, Equatable, Identifiable {
    public var id: String
    public var name: String
    public var stars: Int
    public var nextReviewDate: String?       // "YYYY-MM-DD"
    public var originalDueDate: String?
    public var outOfRotation: Bool
    public var topicId: String
    public var subjectId: String
    public var notes: String?

    static func day(_ text: String?) -> Date? {
        guard let text, text.count >= 10 else { return nil }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: String(text.prefix(10)))
    }

    public var due: Date? { Self.day(nextReviewDate) }

    public func isDue(on today: Date = Date(), calendar: Calendar = .current) -> Bool {
        guard !outOfRotation, let due else { return false }
        return calendar.startOfDay(for: due) <= calendar.startOfDay(for: today)
    }

    public func daysLate(on today: Date = Date(), calendar: Calendar = .current) -> Int {
        guard let due else { return 0 }
        return max(0, calendar.dateComponents([.day], from: calendar.startOfDay(for: due), to: calendar.startOfDay(for: today)).day ?? 0)
    }
}

public enum ReviewRecords {
    public static let tables = ["review_topics", "review_subjects", "review_problems"]

    public static func topic(_ r: SyncRecord) -> ReviewTopic? {
        guard !r.isDeleted, let d = r.data.object, let name = d["name"]?.string else { return nil }
        return ReviewTopic(id: r.syncId, name: name, order: Int(d["orderIndex"]?.number ?? 0))
    }

    public static func subject(_ r: SyncRecord) -> ReviewSubject? {
        guard !r.isDeleted, let d = r.data.object, let name = d["name"]?.string, let topic = d["topicSyncId"]?.string else { return nil }
        return ReviewSubject(id: r.syncId, name: name, color: d["color"]?.string ?? "#5B8DEF", topicId: topic)
    }

    public static func problem(_ r: SyncRecord) -> ReviewProblem? {
        guard !r.isDeleted, let d = r.data.object, let name = d["name"]?.string,
              let topic = d["topicSyncId"]?.string, let subject = d["subjectSyncId"]?.string else { return nil }
        let out = d["outOfRotation"]?.bool ?? ((d["outOfRotation"]?.number ?? 0) != 0)
        return ReviewProblem(id: r.syncId, name: name, stars: Int(d["stars"]?.number ?? 3),
                             nextReviewDate: d["nextReviewDate"]?.string, originalDueDate: d["originalDueDate"]?.string,
                             outOfRotation: out, topicId: topic, subjectId: subject, notes: d["descriptionText"]?.string)
    }
}

/// The wheel that picks which review to do, the same weights as review_wheel.py: late, pushed-back and
/// points-losing reviews get bigger slices.
public enum ReviewWheel {
    public static let breakCost = 3
    static let maxLateDays = 10
    static let maxMoveDays = 7

    public static func pointsLost(_ p: ReviewProblem, today: Date = Date(), calendar: Calendar = .current) -> Int {
        guard let due = p.due else { return 0 }
        let original = ReviewProblem.day(p.originalDueDate) ?? due
        let moved = min(max(0, calendar.dateComponents([.day], from: original, to: due).day ?? 0), maxMoveDays)
        let overdue = min(p.daysLate(on: today, calendar: calendar), maxMoveDays)
        return (moved + overdue) * 3
    }

    public static func weight(_ p: ReviewProblem, today: Date = Date(), calendar: Calendar = .current) -> Double {
        guard let due = p.due else { return 1 }
        let original = ReviewProblem.day(p.originalDueDate) ?? due
        let late = min(p.daysLate(on: today, calendar: calendar), maxLateDays)
        let moved = due > original
        return 1.0 + Double(late) + (moved ? 2.0 : 0.0) + Double(pointsLost(p, today: today, calendar: calendar)) / 3.0
    }

    /// The winning index and how far through its slice the pointer should stop (0 to 1).
    public static func spin<G: RandomNumberGenerator>(weights: [Double], using rng: inout G) -> (index: Int, position: Double) {
        let total = weights.reduce(0, +)
        var pick = Double.random(in: 0..<max(total, 0.000001), using: &rng)
        for (i, w) in weights.enumerated() {
            if pick < w { return (i, Double.random(in: 0.12...0.88, using: &rng)) }
            pick -= w
        }
        return (max(0, weights.count - 1), 0.5)
    }
}
