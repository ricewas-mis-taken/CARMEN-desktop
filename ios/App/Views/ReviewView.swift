import SwiftUI
import CarmenCore

/// What is due to review, from the same topics and problems as the desktop, and the wheel that picks one.
/// Reviews are done and scored on the computer; here a review is timed as a focus session.
struct ReviewView: View {
    @EnvironmentObject var model: AppModel
    var openFocus: () -> Void
    @State private var wheelItems: [ReviewProblem]?

    private var due: [ReviewProblem] {
        model.problems.filter { $0.isDue() }.sorted { ($0.due ?? .distantFuture) < ($1.due ?? .distantFuture) }
    }

    var body: some View {
        Page(title: "Review", trailing: {
            Button("Spin") { wheelItems = due }.disabled(due.isEmpty)
        }) {
            if due.isEmpty {
                Card {
                    Text(model.problems.isEmpty ? "Sign in under Settings to see your reviews." : "Nothing due today.")
                        .foregroundStyle(Theme.muted)
                }
            }
            ForEach(model.topics) { topic in
                let items = due.filter { $0.topicId == topic.id }
                if !items.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(topic.name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
                        Card {
                            VStack(alignment: .leading, spacing: 14) {
                                ForEach(items) { problem in
                                    row(problem)
                                    if problem.id != items.last?.id { Divider() }
                                }
                            }
                        }
                    }
                }
            }
        }
        .refreshable { await model.sync() }
        .sheet(isPresented: Binding(get: { wheelItems != nil }, set: { if !$0 { wheelItems = nil } })) {
            WheelSheet(problems: wheelItems ?? []) { problem in
                wheelItems = nil
                startTimed(problem)
            }
        }
    }

    private func row(_ problem: ReviewProblem) -> some View {
        let subject = model.subjects.first { $0.id == problem.subjectId }
        let late = problem.daysLate()
        return HStack(spacing: 12) {
            Circle().fill(Color(hex: subject?.color ?? "#5B8DEF")).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                Text(problem.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                Text(late > 0 ? "\(late) day\(late == 1 ? "" : "s") late" : "Due today")
                    .font(.footnote).foregroundStyle(late > 0 ? Color.red : Theme.muted)
            }
            Spacer()
            Text(String(repeating: "★", count: max(0, min(5, problem.stars)))).font(.caption).foregroundStyle(.orange)
            Button { startTimed(problem) } label: { Image(systemName: "play.fill").font(.subheadline) }
                .accessibilityLabel("Time \(problem.name)")
        }
    }

    private func startTimed(_ problem: ReviewProblem) {
        let topic = model.topics.first { $0.id == problem.topicId }
        model.launchTitle = [topic?.name, problem.name].compactMap { $0 }.joined(separator: ": ")
        openFocus()
    }
}

/// The wheel: names only, a slice for each review, bigger for the ones costing you points.
struct WheelSheet: View {
    var problems: [ReviewProblem]
    var onStart: (ReviewProblem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var angle = 0.0
    @State private var winner: Int?
    @State private var spinning = false

    private var weights: [Double] { problems.map { ReviewWheel.weight($0) } }
    private let colours = ["F5C242", "4F8EF7", "E8576B", "43B581", "9B6BF2", "F2894B", "2DB7C9", "D9589B", "8BC34A", "5C6BC0"]

    var body: some View {
        VStack(spacing: 22) {
            HStack { Spacer(); Button("Close") { dismiss() } }
            ZStack(alignment: .top) {
                WheelShape(weights: weights, colours: colours, names: problems.map(\.name), winner: winner)
                    .rotationEffect(.degrees(angle))
                    .frame(width: 320, height: 320)
                    .padding(.top, 14)
                Image(systemName: "arrowtriangle.down.fill").font(.title).foregroundStyle(Theme.ink)
            }
            if let winner {
                Text(problems[winner].name).font(.title3.weight(.semibold)).foregroundStyle(Theme.ink)
                Button("Time it") { onStart(problems[winner]) }.buttonStyle(PrimaryButtonStyle())
            } else {
                Button("Spin") { spin() }.buttonStyle(PrimaryButtonStyle()).disabled(spinning)
            }
            Spacer()
        }
        .padding(24)
        .background(Theme.page.ignoresSafeArea())
    }

    private func spin() {
        var rng = SystemRandomNumberGenerator()
        let pick = ReviewWheel.spin(weights: weights, using: &rng)
        let total = weights.reduce(0, +)
        let before = weights.prefix(pick.index).reduce(0, +)
        let target = (before + weights[pick.index] * pick.position) / total * 360       // degrees clockwise from the top
        let turns = Double(Int.random(in: 4...6)) * 360
        let end = angle - angle.truncatingRemainder(dividingBy: 360) + turns + (360 - target)
        spinning = true
        winner = nil
        withAnimation(.easeOut(duration: 4.2)) { angle = end }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4.3) {
            spinning = false
            withAnimation { winner = pick.index }
        }
    }
}

struct WheelShape: View {
    var weights: [Double]
    var colours: [String]
    var names: [String]
    var winner: Int?

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            let total = weights.reduce(0, +)
            ZStack {
                ForEach(weights.indices, id: \.self) { i in
                    let start = weights.prefix(i).reduce(0, +) / total * 360
                    let sweep = weights[i] / total * 360
                    Slice(start: start, sweep: sweep)
                        .fill(Color(hex: colours[i % colours.count]).opacity(winner == nil || winner == i ? 1 : 0.35))
                    Slice(start: start, sweep: sweep).stroke(.white, lineWidth: 2)
                    if sweep > 14 {
                        Text(names[i]).font(.system(size: 13, weight: .bold)).foregroundStyle(.white)
                            .lineLimit(1).frame(width: size * 0.36, alignment: .trailing)
                            .offset(x: size * 0.27)
                            .rotationEffect(.degrees(start + sweep / 2 - 90))
                    }
                }
                Circle().fill(.white).frame(width: size * 0.12)
            }
            .frame(width: size, height: size)
        }
    }
}

struct Slice: Shape {
    var start: Double          // degrees clockwise from the top
    var sweep: Double

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let centre = CGPoint(x: rect.midX, y: rect.midY)
        path.move(to: centre)
        path.addArc(center: centre, radius: min(rect.width, rect.height) / 2,
                    startAngle: .degrees(start - 90), endAngle: .degrees(start + sweep - 90), clockwise: false)
        path.closeSubpath()
        return path
    }
}
