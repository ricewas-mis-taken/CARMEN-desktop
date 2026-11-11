import DeviceActivity
import FamilyControls
import ManagedSettings
import CarmenCore

/// Runs when the system's own clock says a window opened or closed, even if Carmen was closed or killed.
///  - "carmen.session": the whole session. Its end lifts the shield and files the session.
///  - "carmen.focus.N": one Pomodoro focus block. Its start puts the shield back up after a break, its end lifts it.
final class CarmenMonitor: DeviceActivityMonitor {
    private let settings = ManagedSettingsStore(named: ManagedSettingsStore.Name("carmen"))
    private let focusPrefix = "carmen.focus."

    override func intervalDidStart(for activity: DeviceActivityName) {
        super.intervalDidStart(for: activity)
        guard activity.rawValue.hasPrefix(focusPrefix), let session = SharedStore().session,
              session.isEnforcing(at: Date()) else { return }
        raiseShield(allowedSites: session.allowedSites)
    }

    override func intervalDidEnd(for activity: DeviceActivityName) {
        super.intervalDidEnd(for: activity)
        let store = SharedStore()
        guard let session = store.session else { return }
        let now = Date()
        if activity.rawValue.hasPrefix(focusPrefix) {
            // A block ended: it is break time unless the whole session is over, which the other window handles.
            if !session.isEnforcing(at: now) { settings.clearAllSettings() }
            return
        }
        // A paused session has no shield up and a new window is scheduled when it resumes.
        guard !session.isPaused(), session.isFinished(at: now) else { return }
        store.file(session.historyEntry(endingAt: now, endType: "natural"))
        store.session = nil
        settings.clearAllSettings()
    }

    private func raiseShield(allowedSites: [String]) {
        guard let defaults = UserDefaults(suiteName: SharedStore.groupId),
              let data = defaults.data(forKey: "carmen.selection"),
              let selection = try? JSONDecoder().decode(FamilyActivitySelection.self, from: data) else { return }
        settings.shield.applications = selection.applicationTokens.isEmpty ? nil : selection.applicationTokens
        settings.shield.applicationCategories = selection.categoryTokens.isEmpty ? nil : .specific(selection.categoryTokens)
        settings.shield.webDomains = selection.webDomainTokens.isEmpty ? nil : selection.webDomainTokens
        let domains = allowedSites.map { $0.lowercased() }.filter { !$0.isEmpty }
        settings.webContent.blockedByFilter = domains.isEmpty ? nil : .all(except: Set(domains.map { WebDomain(domain: $0) }))
    }
}
