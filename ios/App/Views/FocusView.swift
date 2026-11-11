import SwiftUI
import FamilyControls
import CarmenCore

struct FocusView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var session: SessionController
    @EnvironmentObject var blocking: BlockingController

    @State private var minutes = 25
    @State private var customMinutes = ""
    @State private var lock = LockMode.soft
    @State private var task: CarmenTask?
    @State private var sites: [String] = []
    @State private var newSite = ""
    @State private var picking = false
    @State private var asking = false
    @State private var problem: String?

    private let presets = [15, 25, 45, 60, 90]

    var body: some View {
        Page(title: "Focus") {
            Card {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Length").font(.headline).foregroundStyle(Theme.ink)
                    HStack(spacing: 8) {
                        ForEach(presets, id: \.self) { preset in
                            Button("\(preset)") { minutes = preset; customMinutes = "" }
                                .font(.subheadline.weight(.semibold))
                                .frame(maxWidth: .infinity).padding(.vertical, 10)
                                .background(minutes == preset && customMinutes.isEmpty ? Theme.ink : Theme.page,
                                            in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                                .foregroundStyle(minutes == preset && customMinutes.isEmpty ? .white : Theme.ink)
                        }
                    }
                    HStack {
                        TextField("Other minutes", text: $customMinutes)
                            .keyboardType(.numberPad)
                            .padding(10)
                            .background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .onChange(of: customMinutes) { _, text in
                                if let value = Int(text), (1...720).contains(value) { minutes = value }
                            }
                        Text("minutes").foregroundStyle(Theme.muted)
                    }
                }
            }

            Card {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Lock").font(.headline).foregroundStyle(Theme.ink)
                    Picker("Lock", selection: $lock) {
                        ForEach(LockMode.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
            }

            Card {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Task").font(.headline).foregroundStyle(Theme.ink)
                    Menu {
                        Button("No task") { task = nil }
                        ForEach(model.tasks) { item in
                            Button(item.name) { choose(item) }
                        }
                    } label: {
                        HStack {
                            Text(task?.name ?? "No task").foregroundStyle(Theme.ink)
                            Spacer()
                            Image(systemName: "chevron.up.chevron.down").font(.footnote).foregroundStyle(Theme.faint)
                        }
                        .padding(12)
                        .background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
            }

            Card {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Block").font(.headline).foregroundStyle(Theme.ink)
                    Button {
                        if blocking.isAuthorized { picking = true } else { Task { await authorize(thenPick: true) } }
                    } label: {
                        HStack {
                            Text(blocking.hasSelection ? summary : "Choose apps and websites").foregroundStyle(Theme.ink)
                            Spacer()
                            Image(systemName: "chevron.right").font(.footnote).foregroundStyle(Theme.faint)
                        }
                        .padding(12)
                        .background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    Text("Only these websites").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.muted)
                    ForEach(sites, id: \.self) { site in
                        HStack {
                            Text(site).foregroundStyle(Theme.ink)
                            Spacer()
                            Button { sites.removeAll { $0 == site } } label: { Image(systemName: "xmark.circle.fill") }
                                .foregroundStyle(Theme.faint)
                                .accessibilityLabel("Remove \(site)")
                        }
                    }
                    HStack {
                        TextField("example.com", text: $newSite)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                            .padding(10)
                            .background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .onSubmit(addSite)
                        Button("Add", action: addSite).disabled(newSite.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }

            if let problem {
                Text(problem).font(.footnote).foregroundStyle(.red)
            }
            Button("Start session") { begin() }
                .buttonStyle(PrimaryButtonStyle())
        }
        .familyActivityPicker(isPresented: $picking, selection: $blocking.selection)
        .onAppear(perform: pickUpLaunchTask)
        .onChange(of: model.launchTask) { _, _ in pickUpLaunchTask() }
    }

    private var summary: String {
        let apps = blocking.selection.applicationTokens.count
        let cats = blocking.selection.categoryTokens.count
        let webs = blocking.selection.webDomainTokens.count
        return [apps > 0 ? "\(apps) app\(apps == 1 ? "" : "s")" : nil,
                cats > 0 ? "\(cats) categor\(cats == 1 ? "y" : "ies")" : nil,
                webs > 0 ? "\(webs) site\(webs == 1 ? "" : "s")" : nil].compactMap { $0 }.joined(separator: ", ")
    }

    private func choose(_ item: CarmenTask) {
        task = item
        lock = item.lockMode
        if let goal = item.goalMinutes(on: Date()), goal > 0 { minutes = min(goal, 720); customMinutes = "" }
    }

    private func pickUpLaunchTask() {
        guard let launched = model.launchTask else { return }
        choose(launched)
        model.launchTask = nil
    }

    private func addSite() {
        let cleaned = newSite.trimmingCharacters(in: .whitespaces).lowercased()
            .replacingOccurrences(of: "https://", with: "").replacingOccurrences(of: "http://", with: "")
            .replacingOccurrences(of: "www.", with: "")
        guard !cleaned.isEmpty, !sites.contains(cleaned) else { newSite = ""; return }
        sites.append(cleaned)
        newSite = ""
    }

    private func authorize(thenPick: Bool) async {
        do {
            try await blocking.requestAuthorization()
            problem = nil
            if thenPick { picking = true }
        } catch {
            problem = "Screen Time access was not given. Without it Carmen can time a session but cannot block apps."
        }
    }

    private func begin() {
        Task {
            if !blocking.isAuthorized { await authorize(thenPick: false) }
            session.start(minutes: minutes, lock: lock, task: task, allowedSites: sites)
        }
    }
}
