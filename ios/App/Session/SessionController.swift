import Foundation
import Combine
import CarmenCore

/// Starts, pauses, resumes and ends the focus session, and keeps the shield and the saved state in step.
@MainActor
final class SessionController: ObservableObject {
    @Published private(set) var state: SessionState?
    @Published private(set) var now = Date()

    private let store: SharedStore
    private let blocking: BlockingController
    private let onFinished: () -> Void
    private var ticker: Timer?

    init(store: SharedStore, blocking: BlockingController, onFinished: @escaping () -> Void) {
        self.store = store
        self.blocking = blocking
        self.onFinished = onFinished
        state = store.session
        startTicking()
        reconcile()
    }

    var isRunning: Bool { state != nil }
    var remaining: TimeInterval { state?.remainingSeconds(at: now) ?? 0 }
    var fraction: Double {
        guard let state else { return 0 }
        return min(1, state.focusedSeconds(at: now) / TimeInterval(state.plannedSeconds))
    }

    func start(minutes: Int, lock: LockMode, task: CarmenTask?, allowedSites: [String]) {
        guard state == nil else { return }
        let begun = Date()
        let session = SessionState(start: begun, plannedSeconds: minutes * 60, lockMode: lock,
                                   taskId: task?.id, title: task?.name,
                                   allowedSites: allowedSites + (task?.domainWhitelist ?? []))
        store.session = session
        state = session
        if blocking.isAuthorized {
            blocking.raiseShield(allowedSites: session.allowedSites)
            blocking.scheduleEnd(from: begun, to: session.projectedEnd(at: begun))
        }
    }

    func pause(reason: String) -> SessionState.PauseResult {
        guard var session = state else { return .alreadyPaused }
        let result = session.pause(reason: reason, at: Date())
        if result == .paused {
            save(session)
            blocking.lowerShield()
            blocking.cancelSchedule()
        }
        return result
    }

    func resume() {
        guard var session = state, session.isPaused() else { return }
        session.resume(at: Date())
        save(session)
        if blocking.isAuthorized {
            blocking.raiseShield(allowedSites: session.allowedSites)
            blocking.scheduleEnd(from: Date(), to: session.projectedEnd(at: Date()))
        }
    }

    /// Ends the session now. "manual" is what the desktop calls ending it yourself.
    func end() {
        finish(endType: "manual")
    }

    /// Called when the app comes back to the front and on every tick: finishes a session whose time ran out
    /// (possibly while the app was closed, or after an extension already filed it) and settles a spent pause.
    func reconcile() {
        guard var session = store.session ?? state else {
            if state != nil { state = nil }
            return
        }
        let moment = Date()
        if store.session == nil, state != nil {                  // an extension ended it while we were away
            state = nil
            blocking.lowerShield()
            onFinished()
            return
        }
        if session.isPaused() {
            let before = session
            session.settleBudget(at: moment)
            if session != before {
                save(session)
                if blocking.isAuthorized { blocking.raiseShield(allowedSites: session.allowedSites) }
            }
        }
        state = session
        if session.isFinished(at: moment) { finish(endType: "natural") }
    }

    private func finish(endType: String) {
        guard let session = store.session ?? state else { return }
        store.file(session.historyEntry(endingAt: Date(), endType: endType))
        store.session = nil
        state = nil
        blocking.lowerShield()
        blocking.cancelSchedule()
        onFinished()
    }

    private func save(_ session: SessionState) {
        store.session = session
        state = session
    }

    private func startTicking() {
        ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.now = Date()
                if self.state != nil { self.reconcile() }
            }
        }
    }
}
