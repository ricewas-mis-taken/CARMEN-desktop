import SwiftUI
import CarmenCore

struct HistoryView: View {
    @EnvironmentObject var model: AppModel

    private var days: [(day: Date, entries: [HistoryEntry])] {
        let calendar = Calendar.current
        let visible = model.history.filter { !$0.beside }.sorted { $0.end > $1.end }.prefix(300)
        let grouped = Dictionary(grouping: visible) { calendar.startOfDay(for: $0.start) }
        return grouped.keys.sorted(by: >).map { (day: $0, entries: grouped[$0] ?? []) }
    }

    var body: some View {
        Page(title: "History") {
            if days.isEmpty {
                Card { Text("No sessions yet.").foregroundStyle(Theme.muted) }
            }
            ForEach(days, id: \.day) { group in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(group.day, format: .dateTime.weekday(.wide).month().day())
                        Spacer()
                        Text(minutesText(Int(group.entries.reduce(0) { $0 + $1.seconds } / 60)))
                    }
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
                    Card {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(group.entries) { entry in
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(entry.title ?? "Focus").font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                                        Text(entry.start, format: .dateTime.hour().minute()).font(.footnote).foregroundStyle(Theme.muted)
                                    }
                                    Spacer()
                                    Text(minutesText(Int(entry.seconds / 60))).foregroundStyle(Theme.ink)
                                }
                            }
                        }
                    }
                }
            }
        }
        .refreshable { await model.sync() }
    }
}
