import SwiftUI
import CarmenCore

/// The shared bullet-point note: the same list as QuickNote on the computer.
struct NoteView: View {
    @EnvironmentObject var model: AppModel
    @State private var draft = ""
    @FocusState private var typing: Bool

    var body: some View {
        Page(title: "QuickNote") {
            Card {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        TextField("Add a line", text: $draft)
                            .focused($typing)
                            .submitLabel(.done)
                            .onSubmit(add)
                        Button("Add", action: add).disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    Divider()
                    if model.note.isEmpty {
                        Text("Nothing jotted down yet.").foregroundStyle(Theme.muted)
                    }
                    ForEach(Array(model.note.enumerated()), id: \.offset) { index, line in
                        HStack(alignment: .top) {
                            Text("•").foregroundStyle(Theme.faint)
                            Text(line).foregroundStyle(Theme.ink)
                            Spacer()
                            Button { remove(index) } label: { Image(systemName: "xmark") }
                                .font(.footnote).foregroundStyle(Theme.faint)
                                .accessibilityLabel("Remove line")
                        }
                    }
                }
            }
        }
        .refreshable { await model.pullNote() }
        .task { await model.pullNote() }
    }

    private func add() {
        let line = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        model.saveNote(model.note + [line])
        draft = ""
    }

    private func remove(_ index: Int) {
        var items = model.note
        guard items.indices.contains(index) else { return }
        items.remove(at: index)
        model.saveNote(items)
    }
}
