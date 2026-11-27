import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var session: SessionController
    @State private var tab = Tab.home

    enum Tab: Hashable { case home, focus, tasks, review, note, screenTime, history, settings }

    var body: some View {
        TabView(selection: $tab) {
            HomeView(openFocus: { tab = .focus })
                .tabItem { Label("Home", systemImage: "house") }.tag(Tab.home)
            FocusView()
                .tabItem { Label("Focus", systemImage: "scope") }.tag(Tab.focus)
            TasksView(openFocus: { tab = .focus })
                .tabItem { Label("Tasks", systemImage: "checkmark.circle") }.tag(Tab.tasks)
            ReviewView(openFocus: { tab = .focus })
                .tabItem { Label("Review", systemImage: "arrow.triangle.2.circlepath") }.tag(Tab.review)
            NoteView()
                .tabItem { Label("QuickNote", systemImage: "note.text") }.tag(Tab.note)
            ScreenTimeView()
                .tabItem { Label("Screen Time", systemImage: "clock") }.tag(Tab.screenTime)
            HistoryView()
                .tabItem { Label("History", systemImage: "list.bullet") }.tag(Tab.history)
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }.tag(Tab.settings)
        }
        .fullScreenCover(isPresented: .constant(session.isRunning)) {
            SessionView()
                .environmentObject(model)
                .environmentObject(session)
        }
    }
}

/// The page behind every tab: the soft violet the desktop uses.
struct Page<Trailing: View, Content: View>: View {
    var title: String
    var trailing: Trailing
    var content: Content

    init(title: String, @ViewBuilder trailing: () -> Trailing, @ViewBuilder content: () -> Content) {
        self.title = title; self.trailing = trailing(); self.content = content()
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) { content }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
            }
            .background(Theme.page.ignoresSafeArea())
            .navigationTitle(title)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { trailing } }
            .scrollIndicators(.hidden)
        }
    }
}

extension Page where Trailing == EmptyView {
    init(title: String, @ViewBuilder content: () -> Content) {
        self.init(title: title, trailing: { EmptyView() }, content: content)
    }
}
