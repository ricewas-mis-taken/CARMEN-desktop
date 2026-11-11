import ManagedSettings
import ManagedSettingsUI
import UIKit
import CarmenCore

/// What the person sees when they open something that is blocked.
final class CarmenShieldConfiguration: ShieldConfigurationDataSource {
    private func configuration() -> ShieldConfiguration {
        let session = SharedStore().session
        let hard = session?.lockMode == .hard
        let left = session.map { clock($0.remainingSeconds(at: Date())) } ?? ""
        let ink = UIColor(red: 0x24 / 255, green: 0x1B / 255, blue: 0x4F / 255, alpha: 1)
        return ShieldConfiguration(
            backgroundBlurStyle: .systemUltraThinMaterialDark,
            backgroundColor: ink.withAlphaComponent(0.92),
            icon: UIImage(systemName: "lock.fill"),
            title: ShieldConfiguration.Label(text: "Locked in", color: .white),
            subtitle: ShieldConfiguration.Label(text: left.isEmpty ? "" : "\(left) left", color: UIColor.white.withAlphaComponent(0.75)),
            primaryButtonLabel: ShieldConfiguration.Label(text: "Back to focus", color: ink),
            primaryButtonBackgroundColor: .white,
            secondaryButtonLabel: hard ? nil : ShieldConfiguration.Label(text: "Continue anyway", color: .white))
    }

    private func clock(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.up)))
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%02d:%02d", m, s)
    }

    override func configuration(shielding application: Application) -> ShieldConfiguration { configuration() }
    override func configuration(shielding application: Application, in category: ActivityCategory) -> ShieldConfiguration { configuration() }
    override func configuration(shielding webDomain: WebDomain) -> ShieldConfiguration { configuration() }
    override func configuration(shielding webDomain: WebDomain, in category: ActivityCategory) -> ShieldConfiguration { configuration() }
}
