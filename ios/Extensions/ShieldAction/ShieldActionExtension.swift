import ManagedSettings
import CarmenCore

/// The two buttons on the shield. "Back to focus" just leaves. In a soft lock, "Continue anyway" counts as a slip,
/// lifts the shield for that one app, site or category until the session ends, and is logged on the session.
final class CarmenShieldAction: ShieldActionDelegate {
    private let settings = ManagedSettingsStore(named: ManagedSettingsStore.Name("carmen"))

    private func handle(_ action: ShieldAction, release: () -> Void) -> ShieldActionResponse {
        switch action {
        case .primaryButtonPressed:
            return .close
        case .secondaryButtonPressed:
            let store = SharedStore()
            guard var session = store.session, session.lockMode == .soft else { return .close }   // a hard lock never lets go
            session.violations += 1
            session.releasedApps += 1
            store.session = session
            release()
            return .defer
        @unknown default:
            return .close
        }
    }

    override func handle(action: ShieldAction, for application: ApplicationToken,
                         completionHandler: @escaping (ShieldActionResponse) -> Void) {
        completionHandler(handle(action) {
            settings.shield.applications = (settings.shield.applications ?? []).subtracting([application])
        })
    }

    override func handle(action: ShieldAction, for webDomain: WebDomainToken,
                         completionHandler: @escaping (ShieldActionResponse) -> Void) {
        completionHandler(handle(action) {
            settings.shield.webDomains = (settings.shield.webDomains ?? []).subtracting([webDomain])
        })
    }

    override func handle(action: ShieldAction, for category: ActivityCategoryToken,
                         completionHandler: @escaping (ShieldActionResponse) -> Void) {
        completionHandler(handle(action) {
            if case .specific(let categories, let except) = settings.shield.applicationCategories {
                settings.shield.applicationCategories = .specific(categories.subtracting([category]), except: except)
            }
        })
    }
}
