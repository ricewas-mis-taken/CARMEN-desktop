import SwiftUI
import CarmenCore

struct TasksView: View {
    @EnvironmentObject var model: AppModel
    var openFocus: () -> Void
    @State private var editing: CarmenTask?
    @State private var isNew = false

    var body: some View {
        Page(title: "Tasks", trailing: {
            Button {
                let used = Set(model.tasks.map { $0.color.lowercased() })
                let color = CarmenTask.palette.first { !used.contains($0.lowercased()) } ?? CarmenTask.palette[0]
                editing = model.newTask(name: "", color: color, minutes: 30, lock: .soft)
                isNew = true
            } label: { Image(systemName: "plus") }
            .accessibilityLabel("Add task")
        }) {
            if model.tasks.isEmpty {
                Card {
                    Text(model.email == nil ? "Sign in under Settings to see your tasks." : "No tasks yet.")
                        .foregroundStyle(Theme.muted)
                }
            } else {
                Card {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(model.tasks) { task in
                            TaskProgressRow(task: task) {
                                model.launchTask = task
                                openFocus()
                            }
                            .contentShape(Rectangle())
                            .onTapGesture { editing = task; isNew = false }
                            if task.id != model.tasks.last?.id { Divider() }
                        }
                    }
                }
            }
        }
        .refreshable { await model.sync() }
        .sheet(item: $editing) { task in
            TaskEditor(task: task, isNew: isNew)
                .environmentObject(model)
        }
    }
}

struct TaskEditor: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State var task: CarmenTask
    var isNew: Bool
    @State private var confirmingDelete = false
    @State private var site = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: Binding(get: { task.name }, set: { task.name = $0 }))
                    Stepper(value: Binding(get: { task.targetMinutes }, set: { task.setTarget($0) }), in: 5...720, step: 5) {
                        Text("Daily goal \(minutesText(task.targetMinutes))")
                    }
                    Picker("Lock", selection: Binding(get: { task.lockMode }, set: { task.lockMode = $0 })) {
                        ForEach(LockMode.allCases) { Text($0.title).tag($0) }
                    }
                }
                Section("Colour") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 5), spacing: 12) {
                        ForEach(CarmenTask.palette, id: \.self) { hex in
                            Circle().fill(Color(hex: hex)).frame(width: 34, height: 34)
                                .overlay(Circle().stroke(Theme.ink, lineWidth: task.color.lowercased() == hex.lowercased() ? 3 : 0))
                                .onTapGesture { task.color = hex }
                        }
                    }
                }
                Section("Allowed websites") {
                    ForEach(task.domainWhitelist, id: \.self) { item in Text(item) }
                        .onDelete { offsets in
                            var list = task.domainWhitelist
                            list.remove(atOffsets: offsets)
                            task.domainWhitelist = list
                        }
                    HStack {
                        TextField("example.com", text: $site)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        Button("Add") {
                            let cleaned = site.trimmingCharacters(in: .whitespaces).lowercased()
                            if !cleaned.isEmpty, !task.domainWhitelist.contains(cleaned) { task.domainWhitelist += [cleaned] }
                            site = ""
                        }.disabled(site.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                if !isNew {
                    Section {
                        Button("Delete task", role: .destructive) { confirmingDelete = true }
                    }
                }
            }
            .navigationTitle(isNew ? "New task" : "Edit task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { model.save(task); dismiss() }
                        .disabled(task.name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .confirmationDialog("Delete this task?", isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { model.delete(task); dismiss() }
            }
        }
    }
}
