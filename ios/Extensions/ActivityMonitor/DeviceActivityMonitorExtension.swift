import DeviceActivity
import ManagedSettings
import CarmenCore

/// Runs when the system says the session window is over, even if Carmen was closed or killed.
/// It takes the shield down and files the session so the app can send it next time it opens.
final class CarmenMonitor: DeviceActivityMonitor {
    override func intervalDidEnd(for activity: DeviceActivityName) {
        super.intervalDidEnd(for: activity)
        let store = SharedStore()
        guard let session = store.session else { return }
        let now = Date()
        // A paused session has no shield up and a new window is scheduled when it resumes.
        guard !session.isPaused(), session.isFinished(at: now) else { return }
        store.file(session.historyEntry(endingAt: now, endType: "natural"))
        store.session = nil
        ManagedSettingsStore(named: ManagedSettingsStore.Name("carmen")).clearAllSettings()
    }
}
