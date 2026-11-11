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
    public static let pulledTables = ["tasks", "session_history"]

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

    public struct Result: Equatable {
        public var tasks: [SharedStore.CachedTask]
        public var history: [HistoryEntry]
        public var sent: Int
    }

    /// Sends what finished here, then reads what changed anywhere. Safe to call as often as you like.
    @discardableResult
    public func sync() async throws -> Result {
        let session = try await validSession()
        let waiting = store.outbox
        try await client.push(waiting.map { $0.record(deviceId: store.deviceId) }, userId: session.userId, token: session.accessToken)
        store.clearOutbox(sent: Set(waiting.map(\.id)))

        var cache = store.cache
        let page = try await client.pull(tables: Self.pulledTables, cursor: cache.cursor, token: session.accessToken)
        var tasks = Dictionary(uniqueKeysWithValues: cache.tasks.map { ($0.id, $0) })
        var history = Dictionary(uniqueKeysWithValues: cache.history.map { ($0.id, $0) })
        for record in page.records {
            switch record.table {
            case "tasks":
                let task = CarmenTask(record: record)
                if task.isDeleted || task.archived || task.name.isEmpty { tasks[task.id] = nil }
                else { tasks[task.id] = SharedStore.CachedTask(task) }
            case "session_history":
                if record.isDeleted { history[record.syncId] = nil }
                else if let entry = HistoryEntry(record: record) { history[entry.id] = SharedStore.Entry(entry) }
            default: break
            }
        }
        cache.tasks = tasks.values.sorted { $0.name.lowercased() < $1.name.lowercased() }
        cache.history = history.values.sorted { $0.end < $1.end }
        cache.cursor = page.cursor
        store.cache = cache
        return Result(tasks: cache.tasks, history: cache.history.map(\.history), sent: waiting.count)
    }
}
