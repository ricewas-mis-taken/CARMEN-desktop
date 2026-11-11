import Foundation
import SwiftUI
import CarmenCore

/// What every screen reads: the cached tasks and history, the sign-in, the running session.
@MainActor
final class AppModel: ObservableObject {
    let store = SharedStore()
    let blocking = BlockingController()
    private(set) var sessions: SessionController!
    private var engine: SyncEngine?

    @Published var tasks: [CarmenTask] = []
    @Published var history: [HistoryEntry] = []
    @Published var email: String?
    @Published var syncing = false
    @Published var message: String?
    @Published var launchTask: CarmenTask?

    var configured: Bool { engine != nil }

    init() {
        let info = Bundle.main.infoDictionary ?? [:]
        if let urlText = info["CarmenSupabaseURL"] as? String, let url = URL(string: urlText),
           let key = info["CarmenSupabaseKey"] as? String, !key.isEmpty, !urlText.contains("$(") {
            engine = SyncEngine(client: SupabaseClient(baseURL: url, key: key), store: store, vault: KeychainVault())
        }
        sessions = SessionController(store: store, blocking: blocking) { [weak self] in
            Task { await self?.sync() }
        }
        let cache = store.cache
        tasks = cache.tasks
        history = cache.history.map(\.history) + store.outbox
        Task { await refreshAccount() }
    }

    func refreshAccount() async {
        email = await engine?.signedInEmail
    }

    func signIn(email: String, password: String) async {
        guard let engine else { message = "This build has no cloud address. See ios/README.md."; return }
        syncing = true
        defer { syncing = false }
        do {
            try await engine.signIn(email: email, password: password)
            await refreshAccount()
            message = nil
            await sync()
        } catch CloudError.badCredentials {
            message = "That email or password is not right."
        } catch CloudError.network {
            message = "No connection. Try again."
        } catch {
            message = "Could not sign in."
        }
    }

    func signOut() async {
        await engine?.signOut()
        tasks = []
        history = store.outbox
        await refreshAccount()
    }

    func sync() async {
        guard let engine, email != nil, !syncing else { return }
        syncing = true
        defer { syncing = false }
        do {
            let result = try await engine.sync()
            tasks = result.tasks
            history = result.history + store.outbox
            message = nil
        } catch CloudError.badCredentials {
            email = nil
            message = "Signed out. Sign in again."
        } catch {
            message = "Offline. Sessions are kept and sent later."
            history = store.cache.history.map(\.history) + store.outbox
        }
    }

    // MARK: tasks

    /// Saves an edit or a new task here at once and sends it with the next sync.
    func save(_ task: CarmenTask) {
        var edited = task
        edited.updatedAt = Date()
        edited.deviceId = store.deviceId
        if let index = tasks.firstIndex(where: { $0.id == edited.id }) { tasks[index] = edited } else { tasks.append(edited) }
        tasks.sort { $0.name.lowercased() < $1.name.lowercased() }
        var outbox = store.taskOutbox.filter { $0.id != edited.id }
        outbox.append(edited)
        store.taskOutbox = outbox
        var cache = store.cache
        cache.tasks = tasks
        store.cache = cache
        Task { await sync() }
    }

    func delete(_ task: CarmenTask) {
        var gone = task
        gone.isDeleted = true
        tasks.removeAll { $0.id == task.id }
        var outbox = store.taskOutbox.filter { $0.id != task.id }
        gone.updatedAt = Date()
        gone.deviceId = store.deviceId
        outbox.append(gone)
        store.taskOutbox = outbox
        var cache = store.cache
        cache.tasks = tasks
        store.cache = cache
        Task { await sync() }
    }

    func newTask(name: String, color: String, minutes: Int, lock: LockMode) -> CarmenTask {
        CarmenTask(name: name, color: color, targetMinutes: minutes, lockMode: lock, deviceId: store.deviceId)
    }

    func minutesToday(for task: CarmenTask? = nil) -> Int {
        Int(Progress.seconds(on: Date(), taskId: task?.id, in: history) / 60)
    }

    var streak: Int { Progress.streak(history: history) }
}
