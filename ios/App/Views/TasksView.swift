import SwiftUI
import CarmenCore

struct TasksView: View {
    @EnvironmentObject var model: AppModel
    var openFocus: () -> Void

    var body: some View {
        Page(title: "Tasks") {
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
                            if task.id != model.tasks.last?.id { Divider() }
                        }
                    }
                }
            }
        }
        .refreshable { await model.sync() }
    }
}
