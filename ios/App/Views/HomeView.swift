import SwiftUI
import CarmenCore

struct HomeView: View {
    @EnvironmentObject var model: AppModel
    var openFocus: () -> Void

    private var greeting: String {
        switch Calendar.current.component(.hour, from: Date()) {
        case 5..<12: return "Fresh start. Pick one thing and begin."
        case 12..<18: return "Pick up where you left off."
        case 18..<23: return "Wind down strong. One more block?"
        default: return "Quiet hours. One focused block, then sleep."
        }
    }

    var body: some View {
        Page(title: "Home") {
            Text(greeting)
                .font(.title3.weight(.medium))
                .foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 4)

            HStack(spacing: 14) {
                Card {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(minutesText(model.minutesToday())).font(.system(size: 34, weight: .bold, design: .rounded))
                        Text("focused today").font(.subheadline).foregroundStyle(Theme.muted)
                    }.foregroundStyle(Theme.ink)
                }
                Card {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("\(model.streak)", systemImage: "flame.fill")
                            .font(.system(size: 34, weight: .bold, design: .rounded))
                            .foregroundStyle(model.streak > 0 ? Color.orange : Theme.faint)
                        Text("day streak").font(.subheadline).foregroundStyle(Theme.muted)
                    }
                }
            }

            Button("Start a session") { openFocus() }
                .buttonStyle(PrimaryButtonStyle())

            if !model.tasks.isEmpty {
                Card {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Today").font(.headline).foregroundStyle(Theme.ink)
                        ForEach(model.tasks.prefix(5)) { task in
                            TaskProgressRow(task: task) {
                                model.launchTask = task
                                openFocus()
                            }
                        }
                    }
                }
            }

            if let message = model.message {
                Text(message).font(.footnote).foregroundStyle(Theme.muted)
            }
        }
        .refreshable { await model.sync() }
    }
}

struct TaskProgressRow: View {
    @EnvironmentObject var model: AppModel
    var task: SharedStore.CachedTask
    var start: () -> Void

    private var goal: Int? { task.goalMinutes(on: Date()) }

    var body: some View {
        let done = model.minutesToday(for: task)
        HStack(spacing: 14) {
            ZStack {
                ProgressRing(fraction: goal.map { $0 > 0 ? Double(done) / Double($0) : 1 } ?? 0,
                             color: Color(hex: task.color), lineWidth: 6)
                Text(minutesText(done)).font(.caption2.weight(.semibold)).foregroundStyle(Theme.ink)
            }
            .frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 2) {
                Text(task.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                Text(goal.map { "of \(minutesText($0))" } ?? "no goal today").font(.footnote).foregroundStyle(Theme.muted)
            }
            Spacer()
            Button(action: start) {
                Image(systemName: "play.fill").font(.subheadline)
                    .frame(width: 38, height: 38)
                    .background(Color(hex: task.color), in: Circle())
                    .foregroundStyle(.white)
            }
            .accessibilityLabel("Start \(task.name)")
        }
    }
}
