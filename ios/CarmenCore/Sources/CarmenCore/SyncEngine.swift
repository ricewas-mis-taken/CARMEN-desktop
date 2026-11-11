import Foundation
#if canImport(Security)
import Security
#endif

/// Where the sign-in lives. The app uses the keychain; tests use memory.
public protocol TokenVault: AnyObject {
    func load() -> AuthSession?
    func save(_ session: AuthSession?)
}

public final class MemoryVault: TokenVault {
    private var value: AuthSession?
    public init() {}
    public func load() -> AuthSession? { value }
    public func save(_ session: AuthSession?) { value = session }
}

#if canImport(Security)
public final class KeychainVault: TokenVault {
    private let account = "carmen.auth"
    private let group: String?
    public init(accessGroup: String? = nil) { group = accessGroup }

    private func query() -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: "app.carmenfocus", kSecAttrAccount as String: account]
        if let group { q[kSecAttrAccessGroup as String] = group }
        return q
    }

    public func load() -> AuthSession? {
        var q = query()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(AuthSession.self, from: data)
    }

    public func save(_ session: AuthSession?) {
        SecItemDelete(query() as CFDictionary)
        guard let session else { return }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(session) else { return }
        var q = query()
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(q as CFDictionary, nil)
    }
}
#endif

/// What phase one of the phone shares with the desktop: it reads the tasks and the finished sessions, and it
/// sends the sessions it finished. The running session is not shared yet, so a phone session and a desktop
/// session do not interrupt each other.
public actor SyncEngine {
    public static let pulledTables = ["tasks", "session_history"] + ReviewRecords.tables

    private let client: SupabaseClient
    private let store: SharedStore
    private let vault: TokenVault

    public init(client: SupabaseClient, store: SharedStore, vault: TokenVault) {
        self.client = client; self.store = store; self.vault = vault
    }

    public var signedInEmail: String? { vault.load()?.email }

    public func signIn(email: String, password: String) async throws {
        vault.save(try await client.signIn(email: email, password: password))
    }

    public func signOut() {
        vault.save(nil)
        store.cache = SharedStore.Cache()
    }

    private func validSession() async throws -> AuthSession {
        guard var session = vault.load() else { throw CloudError.badCredentials }
        if session.expiresAt <= Date() {
            do {
                session = try await client.refresh(session)
                vault.save(session)
            } catch CloudError.badCredentials {
                vault.save(nil)                       // the refresh token is no good any more: sign in again
                throw CloudError.badCredentials
            }
        }
        return session
    }

    /// Sends the running session's record (or "it ended") so the desktop sees it within seconds.
    public func pushLive(_ record: SyncRecord) async throws {
        let session = try await validSession()
        try await client.push([record], userId: session.userId, token: session.accessToken)
    }

    /// The running session as the cloud holds it right now.
    public func fetchLive() async throws -> SyncRecord? {
        let session = try await validSession()
        return try await client.fetch(table: LiveWire.table, syncId: LiveWire.syncId, token: session.accessToken)
    }

    /// The shared note, as the cloud holds it right now.
    public func fetchNote() async throws -> [String]? {
        let session = try await validSession()
        guard let record = try await client.fetch(table: QuickNote.table, syncId: QuickNote.syncId, token: session.accessToken) else { return nil }
        return QuickNote.items(from: record)
    }

    public func pushNote(_ items: [String]) async throws {
        let session = try await validSession()
        let record = QuickNote.record(items: items, deviceId: store.deviceId, offset: ServerClock.shared.offset)
        try await client.push([record], userId: session.userId, token: session.accessToken)
    }

    public struct Result: Equatable {
        public var tasks: [CarmenTask]
        public var history: [HistoryEntry]
        public var sent: Int
        public var topics: [ReviewTopic] = []
        public var subjects: [ReviewSubject] = []
        public var problems: [ReviewProblem] = []
    }

    /// Sends what finished here, then reads what changed anywhere. Safe to call as often as you like.
    @discardableResult
    public func sync() async throws -> Result {
        let session = try await validSession()
        let waiting = store.outbox
        let pendingTasks = Dictionary(uniqueKeysWithValues: store.taskOutbox.map { ($0.id, $0) })
        let outgoing = waiting.map { $0.record(deviceId: store.deviceId) }
            + pendingTasks.values.map { $0.record(deviceId: store.deviceId, now: $0.updatedAt ?? Date()) }
        try await client.push(outgoing, userId: session.userId, token: session.accessToken)
        store.clearOutbox(sent: Set(waiting.map(\.id)))
        store.taskOutbox = []

        var cache = store.cache
        let page = try await client.pull(tables: Self.pulledTables, cursor: cache.cursor, token: session.accessToken)
        var tasks = Dictionary(uniqueKeysWithValues: cache.tasks.map { ($0.id, $0) })
        var history = Dictionary(uniqueKeysWithValues: cache.history.map { ($0.id, $0) })
        var topics = Dictionary(uniqueKeysWithValues: cache.topics.map { ($0.id, $0) })
        var subjects = Dictionary(uniqueKeysWithValues: cache.subjects.map { ($0.id, $0) })
        var problems = Dictionary(uniqueKeysWithValues: cache.problems.map { ($0.id, $0) })
        for record in page.records {
            switch record.table {
            case "tasks":
                let task = CarmenTask(record: record)
                if let held = pendingTasks[task.id], (held.updatedAt ?? .distantPast) > (task.updatedAt ?? .distantPast) {
                    continue                                    // an edit made here that has not gone up yet is newer
                }
                if task.isShown { tasks[task.id] = task } else { tasks[task.id] = nil }
            case "session_history":
                if record.isDeleted { history[record.syncId] = nil }
                else if let entry = HistoryEntry(record: record) { history[entry.id] = SharedStore.Entry(entry) }
            case "review_topics": topics[record.syncId] = ReviewRecords.topic(record)
            case "review_subjects": subjects[record.syncId] = ReviewRecords.subject(record)
            case "review_problems": problems[record.syncId] = ReviewRecords.problem(record)
            default: break
            }
        }
        cache.tasks = tasks.values.sorted { $0.name.lowercased() < $1.name.lowercased() }
        cache.history = history.values.sorted { $0.end < $1.end }
        cache.topics = topics.values.sorted { ($0.order, $0.name) < ($1.order, $1.name) }
        cache.subjects = Array(subjects.values)
        cache.problems = Array(problems.values)
        cache.cursor = page.cursor
        store.cache = cache
        return Result(tasks: cache.tasks, history: cache.history.map(\.history), sent: waiting.count,
                      topics: cache.topics, subjects: cache.subjects, problems: cache.problems)
    }
}
