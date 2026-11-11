import Foundation
import FamilyControls
import ManagedSettings
import DeviceActivity
import CarmenCore

/// Everything that touches Screen Time. This is what makes a session a lock: while one is running the chosen
/// apps, categories and websites sit behind a shield, and (with allowed websites) every other site does too.
///
/// Shields live in a named ManagedSettingsStore, so the Screen Time extensions can lift them when the app is
/// not running. Needs the Family Controls entitlement (see ios/README.md).
@MainActor
final class BlockingController: ObservableObject {
    static let storeName = ManagedSettingsStore.Name("carmen")
    static let activityName = DeviceActivityName("carmen.session")

    @Published private(set) var authorization: AuthorizationStatus = AuthorizationCenter.shared.authorizationStatus
    @Published var selection = FamilyActivitySelection() {
        didSet { Self.saveSelection(selection) }
    }

    private let store = ManagedSettingsStore(named: BlockingController.storeName)
    private let center = DeviceActivityCenter()

    init() {
        selection = Self.loadSelection()
    }

    var isAuthorized: Bool { authorization == .approved }

    func refreshAuthorization() {
        authorization = AuthorizationCenter.shared.authorizationStatus
    }

    func requestAuthorization() async throws {
        try await AuthorizationCenter.shared.requestAuthorization(for: .individual)
        refreshAuthorization()
    }

    var hasSelection: Bool {
        !selection.applicationTokens.isEmpty || !selection.categoryTokens.isEmpty || !selection.webDomainTokens.isEmpty
    }

    /// Puts the shield up. `allowedSites` is the allow-only list: when it has anything in it, every other
    /// website is blocked, the same as a task's allowed domains on the desktop.
    func raiseShield(allowedSites: [String]) {
        store.shield.applications = selection.applicationTokens.isEmpty ? nil : selection.applicationTokens
        store.shield.applicationCategories = selection.categoryTokens.isEmpty ? nil : .specific(selection.categoryTokens)
        store.shield.webDomains = selection.webDomainTokens.isEmpty ? nil : selection.webDomainTokens
        let domains = allowedSites.map { $0.trimmingCharacters(in: .whitespaces).lowercased() }.filter { !$0.isEmpty }
        store.webContent.blockedByFilter = domains.isEmpty ? nil : .all(except: Set(domains.map { WebDomain(domain: $0) }))
    }

    func lowerShield() {
        store.clearAllSettings()
    }

    /// Asks the system to run the shield without this app: one window for the whole session, whose end lifts the
    /// shield and files the session, and for a Pomodoro one window per focus block, so breaks open up and the next
    /// block closes in again with Carmen closed. The system wants windows of at least 15 minutes, so a shorter block
    /// is handled by the app itself while it is open.
    func schedule(for session: SessionState, now: Date = Date()) {
        cancelSchedule()
        guard !session.isPaused() else { return }
        let calendar = Calendar.current
        func components(_ date: Date) -> DateComponents { calendar.dateComponents([.hour, .minute, .second], from: date) }
        let minimum: TimeInterval = 15 * 60 + 5
        let end = session.projectedEnd(at: now)
        let whole = DeviceActivitySchedule(intervalStart: components(now),
                                           intervalEnd: components(max(end, now.addingTimeInterval(minimum))), repeats: false)
        try? center.startMonitoring(Self.activityName, during: whole)
        guard let plan = session.pomodoro else { return }
        let running = session.focusedSeconds(at: now)
        for (index, span) in plan.focusSpans().prefix(8).enumerated() where span.end > running && span.end - max(span.start, running) >= minimum {
            let from = now.addingTimeInterval(max(span.start, running) - running)
            let to = now.addingTimeInterval(span.end - running)
            try? center.startMonitoring(DeviceActivityName("carmen.focus.\(index)"),
                                        during: DeviceActivitySchedule(intervalStart: components(from), intervalEnd: components(to), repeats: false))
        }
    }

    func cancelSchedule() {
        center.stopMonitoring()
    }

    // MARK: remembering the choice

    private static let defaults = UserDefaults(suiteName: SharedStore.groupId) ?? .standard
    private static let selectionKey = "carmen.selection"

    private static func saveSelection(_ selection: FamilyActivitySelection) {
        if let data = try? JSONEncoder().encode(selection) { defaults.set(data, forKey: selectionKey) }
    }

    private static func loadSelection() -> FamilyActivitySelection {
        guard let data = defaults.data(forKey: selectionKey),
              let saved = try? JSONDecoder().decode(FamilyActivitySelection.self, from: data) else { return FamilyActivitySelection() }
        return saved
    }
}
