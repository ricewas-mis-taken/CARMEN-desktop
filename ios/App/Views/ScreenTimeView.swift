import SwiftUI
import DeviceActivity
import CarmenCore

/// Today's use of this phone. The numbers are drawn by Apple's report extension; Carmen only hosts the view.
struct ScreenTimeView: View {
    @EnvironmentObject var blocking: BlockingController

    private var today: DeviceActivityFilter {
        DeviceActivityFilter(
            segment: .daily(during: Calendar.current.dateInterval(of: .day, for: Date()) ?? DateInterval(start: Date(), duration: 86_400)),
            users: .all, devices: .init([.iPhone, .iPad]))
    }

    var body: some View {
        Page(title: "Screen Time") {
            if blocking.isAuthorized {
                Card { DeviceActivityReport(DeviceActivityReport.Context("Totals"), filter: today).frame(minHeight: 320) }
            } else {
                Card { Text("Allow Screen Time in Settings to see today's use.").foregroundStyle(Theme.muted) }
            }
        }
    }
}
