import SwiftUI
import CarmenCore

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var blocking: BlockingController
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        Page(title: "Settings") {
            Card {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Account").font(.headline).foregroundStyle(Theme.ink)
                    if let current = model.email {
                        Text(current).foregroundStyle(Theme.muted)
                        Button("Sync now") { Task { await model.sync() } }
                            .buttonStyle(PrimaryButtonStyle()).disabled(model.syncing)
                        Button("Sign out", role: .destructive) { Task { await model.signOut() } }
                    } else {
                        TextField("Email", text: $email)
                            .textContentType(.username).keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .padding(12).background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        SecureField("Password", text: $password)
                            .textContentType(.password)
                            .padding(12).background(Theme.page.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        Button("Sign in") { Task { await model.signIn(email: email, password: password) } }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(email.isEmpty || password.isEmpty || model.syncing)
                    }
                    if let message = model.message { Text(message).font(.footnote).foregroundStyle(Theme.muted) }
                }
            }

            Card {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Screen Time").font(.headline).foregroundStyle(Theme.ink)
                    HStack {
                        Text(blocking.isAuthorized ? "Allowed" : "Not allowed").foregroundStyle(Theme.muted)
                        Spacer()
                        if !blocking.isAuthorized {
                            Button("Allow") { Task { try? await blocking.requestAuthorization() } }
                        }
                    }
                }
            }
        }
    }
}
