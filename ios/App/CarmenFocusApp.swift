import SwiftUI

@main
struct CarmenFocusApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(model.sessions)
                .environmentObject(model.blocking)
                .environmentObject(model.live)
                .tint(Theme.accent)
                .onChange(of: phase) { _, now in
                    guard now == .active else { return }
                    model.blocking.refreshAuthorization()
                    model.sessions.reconcile()
                    Task { await model.sync() }
                }
        }
    }
}
