import Foundation
import SwiftUI
import CarmenCore

/// One session across the phone and the computer. What this phone starts, pauses and ends is sent to the cloud
/// within a moment; what the computer does is read every few seconds. A session running on the computer is shown
/// here, and (if the person wants) shielded on this phone too, so one session covers both.
@MainActor
final class LiveBridge: ObservableObject {
    @Published private(set) var remote: LiveWire.Remote?
    @Published var mirrorDesktop: Bool = UserDefaults.standard.object(forKey: "carmen.mirror") as? Bool ?? true {
        didSet { UserDefaults.standard.set(mirrorDesktop, forKey: "carmen.mirror"); applyMirror() }
    }

    private weak var model: AppModel?
    private var lastStamp: String?
    private var timer: Timer?

    func attach(_ model: AppModel) {
        self.model = model
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.applyMirror()
                if Int(Date().timeIntervalSince1970) % 10 < 5 { await self.poll() }
            }
        }
    }

    func push(_ session: SessionState?, ended: String?) async {
        guard let model, let engine = model.engine, model.email != nil else { return }
        let record = LiveWire.record(session: session, endedSessionId: ended, deviceId: model.store.deviceId,
                                     offset: ServerClock.shared.offset)
        lastStamp = record.updatedAt
        try? await engine.pushLive(record)
    }

    func poll() async {
        guard let model, let engine = model.engine, model.email != nil else { return }
        guard let record = try? await engine.fetchLive(), record.updatedAt != lastStamp else { return }
        lastStamp = record.updatedAt
        if record.deviceId == model.store.deviceId { return }            // our own write coming back
        guard let parsed = LiveWire.parse(record, offset: ServerClock.shared.offset) else { return }
        let sessions = model.sessions!
        switch parsed {
        case .running(let incoming):
            if let local = sessions.state {
                if local.id == incoming.sessionId {
                    sessions.follow(paused: incoming.isPaused)
                    return
                }
                sessions.end(type: "superseded", quiet: true)            // a newer session elsewhere replaces ours
            }
            remote = incoming
        case .idle(let ended, _):
            if let local = sessions.state, ended == local.id { sessions.end(type: "manual", quiet: true) }
            remote = nil
        }
        applyMirror()
    }

    /// While a session runs on the computer and this phone has none of its own, the shield follows it.
    func applyMirror() {
        guard let model, model.sessions.state == nil else { return }
        let blocking = model.blocking
        guard blocking.isAuthorized else { return }
        if mirrorDesktop, let remote, remote.isEnforcing(at: Date()) {
            blocking.raiseShield(allowedSites: remote.domainWhitelist)
        } else {
            blocking.lowerShield()
        }
    }
}
