import SwiftUI
import CarmenCore

/// The running session, full screen. Pause asks why, like the desktop; ending asks twice in a hard lock.
struct SessionView: View {
    @EnvironmentObject var session: SessionController
    @State private var choosingReason = false
    @State private var confirmingEnd = false
    @State private var budgetSpent = false

    private let reasons = ["Break", "Phone call", "Water or food", "Something came up"]

    var body: some View {
        let state = session.state
        ZStack {
            LinearGradient(colors: Theme.sky, startPoint: .top, endPoint: .bottom).ignoresSafeArea()
            VStack(spacing: 28) {
                Spacer()
                Text(state?.title ?? "Focus").font(.title2.weight(.semibold)).foregroundStyle(.white.opacity(0.85))
                ZStack {
                    ProgressRing(fraction: session.fraction, color: .white, lineWidth: 16)
                    VStack(spacing: 6) {
                        Text(clock(session.remaining))
                            .font(.system(size: 64, weight: .semibold, design: .rounded)).monospacedDigit()
                            .contentTransition(.numericText(countsDown: true))
                        Text(statusLine(state)).font(.subheadline).opacity(0.8)
                    }
                    .foregroundStyle(.white)
                }
                .frame(width: 290, height: 290)
                Spacer()

                if state?.isPaused() == true {
                    Button("Resume") { session.resume() }.buttonStyle(PrimaryButtonStyle(color: .white.opacity(0.22)))
                } else {
                    Button("Pause") { choosingReason = true }.buttonStyle(PrimaryButtonStyle(color: .white.opacity(0.22)))
                }
                Button("End session") { confirmingEnd = true }
                    .font(.subheadline.weight(.semibold)).foregroundStyle(.white.opacity(0.7))
            }
            .padding(24)
        }
        .confirmationDialog("Why pause?", isPresented: $choosingReason, titleVisibility: .visible) {
            ForEach(reasons, id: \.self) { reason in
                Button(reason) { if session.pause(reason: reason) == .budgetSpent { budgetSpent = true } }
            }
        }
        .confirmationDialog(state?.lockMode == .hard ? "You chose a hard lock. End it anyway?" : "End this session?",
                            isPresented: $confirmingEnd, titleVisibility: .visible) {
            Button("End session", role: .destructive) { session.end() }
            Button("Keep going", role: .cancel) {}
        }
        .alert("No pause time left", isPresented: $budgetSpent) { Button("OK", role: .cancel) {} }
        .interactiveDismissDisabled()
    }

    private func statusLine(_ state: SessionState?) -> String {
        guard let state else { return "" }
        if state.isPaused() {
            if let left = state.pauseBudgetLeft(at: session.now) { return "Paused, \(clock(left)) of pause left" }
            return "Paused"
        }
        return state.lockMode.title
    }
}
