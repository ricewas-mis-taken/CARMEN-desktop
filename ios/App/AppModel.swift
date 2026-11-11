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

    @Published var tasks: [SharedStore.CachedTask] = []
    @Published var history: [HistoryEntry] = []
    @Published var email: String?
    @Published var syncing = false
    @Published var message: String?
    @Published var launchTask: SharedStore.CachedTask?

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

    func minutesToday(for task: SharedStore.CachedTask? = nil) -> Int {
        Int(Progress.seconds(on: Date(), taskId: task?.id, in: history) / 60)
    }

    var streak: Int { Progress.streak(history: history) }
}
