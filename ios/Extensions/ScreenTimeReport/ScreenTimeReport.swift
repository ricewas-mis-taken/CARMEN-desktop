import DeviceActivity
import ManagedSettings
import SwiftUI

extension DeviceActivityReport.Context {
    static let totals = Self("Totals")
}

/// Today's phone use. The system draws this inside the app but keeps the data away from it, so Carmen shows the
/// numbers without ever being able to read which apps they belong to.
@main
struct CarmenReport: DeviceActivityReportExtension {
    var body: some DeviceActivityReportScene {
        TotalsScene { summary in TotalsView(summary: summary) }
    }
}

struct Summary {
    struct Item: Identifiable {
        let id = UUID()
        let token: ApplicationToken
        let seconds: TimeInterval
    }
    var total: TimeInterval = 0
    var top: [Item] = []
}

struct TotalsScene: DeviceActivityReportScene {
    let context: DeviceActivityReport.Context = .totals
    let content: (Summary) -> TotalsView

    func makeConfiguration(representing data: DeviceActivityResults<DeviceActivityData>) async -> Summary {
        var summary = Summary()
        var perApp: [ApplicationToken: TimeInterval] = [:]
        for await day in data {
            for await segment in day.activitySegments {
                summary.total += segment.totalActivityDuration
                for await category in segment.categories {
                    for await app in category.applications {
                        if let token = app.application.token { perApp[token, default: 0] += app.totalActivityDuration }
                    }
                }
            }
        }
        summary.top = perApp.sorted { $0.value > $1.value }.prefix(5).map { Summary.Item(token: $0.key, seconds: $0.value) }
        return summary
    }
}

struct TotalsView: View {
    let summary: Summary

    private func text(_ seconds: TimeInterval) -> String {
        let minutes = Int(seconds / 60)
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text(text(summary.total)).font(.system(size: 34, weight: .bold, design: .rounded))
                Text("on this device today").font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(summary.top) { item in
                HStack {
                    Label(item.token).labelStyle(.titleAndIcon)
                    Spacer()
                    Text(text(item.seconds)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
