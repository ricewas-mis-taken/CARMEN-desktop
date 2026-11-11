# Carmen Focus for iPhone and iPad

SwiftUI app plus three Screen Time extensions. It signs in to the same Supabase account as the desktop, reads the
tasks and finished sessions, runs focus sessions with a soft or hard lock, and sends the sessions it finished.

**This code has not been compiled yet.** It was written on Windows, where Xcode does not run. Expect a round of
small compile fixes on the first build.

## What is here

| Part | What it does |
|---|---|
| `CarmenCore/` | Swift package, no UI. Data shapes the desktop syncs, the session clock, pause budget, Supabase client, sync, shared storage. Has unit tests (`swift test`). |
| `App/` | The app: Home, Focus, Tasks, History, Settings, and the full-screen running session. |
| `App/Blocking` | Family Controls, Managed Settings and Device Activity: raises the shield, schedules its removal. |
| `Extensions/ActivityMonitor` | Lifts the shield and files the session when its time is up, even if the app was closed. |
| `Extensions/ShieldConfiguration` | The "Locked in" screen on a blocked app. |
| `Extensions/ShieldAction` | "Back to focus", and in a soft lock "Continue anyway" (counted as a slip). |

Lock behaviour:
- **Soft lock**: a blocked app shows a shield with "Continue anyway". It lifts that one app until the session ends and counts a violation.
- **Hard lock**: no way through. Pausing gets a 10 minute budget per session. Ending asks for a second confirmation.
- **Only these websites**: when the list has anything in it, every other website is shielded.

## Not built yet

- Pomodoro cycles, the Review tab, Scribe, QuickNote, Screen Time totals.
- Sharing the *running* session with the desktop. Finished sessions sync both ways; a live session does not, so a phone
  session and a desktop session do not interrupt each other.
- Editing tasks on the phone. Tasks are read-only here.
- Push notifications, widgets, Apple Watch.

## Build it (on a Mac)

1. Install Xcode 15+ and XcodeGen: `brew install xcodegen`.
2. `cp Config.example.xcconfig Config.xcconfig`, then fill in your team id and the two Supabase values (the same ones
   the desktop has in `private/.env`).
3. `xcodegen generate`, then open `CarmenFocus.xcodeproj`.
4. Run the core tests: `cd CarmenCore && swift test`.
5. Fix whatever the compiler finds, then run on a **real device**. Screen Time shielding does not work in the simulator.

## Apple approval (start this first)

Family Controls needs an entitlement Apple grants per app. Request it for the app and for each of the three
extensions at <https://developer.apple.com/contact/request/family-controls-distribution>. Development builds work
on your own devices without it; TestFlight and the App Store need it approved. Approval can take days to weeks.

You also need, in the developer portal: the App Group `group.app.carmenfocus`, and the four bundle ids
`app.carmenfocus.ios`, `.monitor`, `.shieldconfig`, `.shieldaction` with Family Controls and that App Group enabled.

## Known limits of iOS

- A session shorter than 15 minutes is still lifted by the app itself at its end, because the system will not run a
  monitoring window shorter than 15. If the app is killed in that gap, the shield stays until 15 minutes are up.
- Blocking is by what the person picks in Apple's picker. Carmen never sees which apps those are.
- A person can turn Screen Time access off in Settings at any time; a hard lock cannot prevent that.
