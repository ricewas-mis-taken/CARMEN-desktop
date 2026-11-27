import Foundation
import Combine
import CarmenCore

/// Starts, pauses, resumes and ends the focus session, and keeps the shield, the notices and the saved state in step.
@MainActor
final class SessionController: ObservableObject {
    @Published private(set) var state: SessionState?
    @Published private(set) var now = Date()
    /// Set when Screen Time access was turned off in Settings while a session ran.
    @Published var shieldLost = false

    private let store: SharedStore
    private let blocking: BlockingController
    private let onFinished: () -> Void
    /// Called after every change to the running session, with what ended if it just did.
    var onChange: (SessionState?, String?) -> Void = { _, _ in }
    private var ticker: Timer?
    private var wasEnforcing = false

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
    var phase: PomodoroPlan.Phase? { state?.phase(at: now) }

    /// The ring: the whole session, or in a Pomodoro the block that is running now.
    var fraction: Double {
        guard let state else { return 0 }
        if let phase { return 1 - phase.remaining / max(1, phase.length) }
        return min(1, state.focusedSeconds(at: now) / TimeInterval(state.plannedSeconds))
    }

    var displayedSeconds: TimeInterval { phase?.remaining ?? remaining }

    func start(minutes: Int, lock: LockMode, task: CarmenTask?, allowedSites: [String], pomodoro: PomodoroPlan? = nil, title: String? = nil) {
        guard state == nil else { return }
        let begun = Date()
        let session = SessionState(start: begun, plannedSeconds: minutes * 60, lockMode: lock,
                                   taskId: task?.id, title: title ?? task?.name,
                                   allowedSites: allowedSites + (task?.domainWhitelist ?? []), pomodoro: pomodoro)
        store.session = session
        state = session
        shieldLost = false
        Notifier.ask()
        applyShield(for: session)
        onChange(session, nil)
    }

    func pause(reason: String) -> SessionState.PauseResult {
        guard var session = state else { return .alreadyPaused }
        let result = session.pause(reason: reason, at: Date())
        if result == .paused {
            save(session)
            applyShield(for: session)
            onChange(session, nil)
        }
        return result
    }

    func resume() {
        guard var session = state, session.isPaused() else { return }
        session.resume(at: Date())
        save(session)
        applyShield(for: session)
        onChange(session, nil)
    }

    /// Ends the session now. "manual" is what the desktop calls ending it yourself.
    /// `quiet` is for when the end came from the cloud, so it is not sent straight back.
    func end(type: String = "manual", quiet: Bool = false) {
        finish(endType: type, quiet: quiet)
    }

    /// The desktop (or another phone) changed the session this phone started: take its pause or resume.
    func follow(paused: Bool) {
        guard var session = state else { return }
        if paused, !session.isPaused() { session.pause(reason: "break", at: Date()) }
        else if !paused, session.isPaused() { session.resume(at: Date()) }
        else { return }
        save(session)
        applyShield(for: session)
    }

    /// Called when the app comes back to the front and on every tick: finishes a session whose time ran out
    /// (possibly while the app was closed, or after an extension already filed it), settles a spent pause,
    /// and lifts or raises the shield when a Pomodoro block turns into a break or back.
    func reconcile() {
        blocking.refreshAuthorization()
        if store.session == nil, state != nil {                  // an extension ended it while we were away
            state = nil
            blocking.lowerShield()
            Notifier.cancel()
            onFinished()
            onChange(nil, nil)
            return
        }
        guard var session = store.session ?? state else { return }
        let moment = Date()
        if session.isPaused() {
            let before = session
            session.settleBudget(at: moment)
            if session != before { save(session) }
        }
        state = session
        if session.isFinished(at: moment) { finish(endType: "natural"); return }
        let enforcing = session.isEnforcing(at: moment)
        if enforcing != wasEnforcing { applyShield(for: session) }
        if !blocking.isAuthorized, !shieldLost, !session.isPaused() {
            shieldLost = true                                     // Screen Time was switched off: count it as a slip
            session.violations += 1
            save(session)
        }
    }

    private func applyShield(for session: SessionState) {
        let enforcing = session.isEnforcing(at: Date())
        wasEnforcing = enforcing
        guard blocking.isAuthorized else { return }
        if enforcing { blocking.raiseShield(allowedSites: session.allowedSites) } else { blocking.lowerShield() }
        blocking.schedule(for: session)
        Notifier.schedule(for: session)
    }

    private func finish(endType: String, quiet: Bool = false) {
        guard let session = store.session ?? state else { return }
        store.file(session.historyEntry(endingAt: Date(), endType: endType))
        store.session = nil
        state = nil
        wasEnforcing = false
        blocking.lowerShield()
        blocking.cancelSchedule()
        Notifier.cancel()
        onFinished()
        if !quiet { onChange(nil, session.id) }
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
