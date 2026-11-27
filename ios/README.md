# Carmen Focus for iPhone and iPad

SwiftUI app plus Screen Time extensions. It signs in to the same Supabase account as the desktop and shares tasks,
finished sessions, the **running session**, the review list and QuickNote with it.

**This code has not been compiled yet.** It was written on Windows, where Xcode does not run. Expect a round of
small compile fixes on the first build, and check the wheel and the screens by eye.

## What is here

| Part | What it does |
|---|---|
| `CarmenCore/` | Swift package, no UI. Data shapes the desktop syncs, the session clock and Pomodoro phases, the shared running session, the review wheel weights, QuickNote, the Supabase client and sync. Has unit tests (`swift test`). |
| `App/` | Home, Focus, Tasks, Review, QuickNote, Screen Time, History, Settings, and the full-screen running session. |
| `App/Blocking` | Family Controls, Managed Settings and Device Activity: raises and lowers the shield, schedules system-run windows. |
| `App/LiveBridge.swift` | Sends this phone's session to the cloud and reads the computer's, every few seconds. |
| `Extensions/ActivityMonitor` | Runs when the system's own windows open or close: lifts the shield and files the session at its end, brings the shield back after a Pomodoro break, all with the app closed. |
| `Extensions/ShieldConfiguration`, `ShieldAction` | The "Locked in" screen and its buttons. |
| `Extensions/ScreenTimeReport` | Draws today's phone use inside the app. |

What works across both:
- **Tasks**: add, edit (name, daily goal, lock, colour, allowed websites) and delete on the phone. Every field the phone
  does not know about is kept, so the desktop gets the task back intact.
- **Running session**: start, pause, resume and end on the phone and the computer sees it within seconds, and the other way round.
  A newer session on one replaces the older one on the other, the same rule the desktop uses between two computers.
  A session running on the computer is shown on the phone, and the phone can shield itself for it too (a setting).
- **Pomodoro**: cycles of focus and break. The shield comes down for each break and back up for the next block, by
  the system's own clock when the block is 15 minutes or longer, and by the app otherwise. Notices say when a block ends.
- **Review**: due problems by topic, and the wheel (same weights as the desktop's). A review is *timed* here as a
  focus session; doing and scoring it is done on the computer.
- **QuickNote**: the same bullet list as the desktop, editable here.
- **Screen Time**: today's use of this phone, drawn by Apple's report extension.
- **Lock behaviour**: soft lock has a "Continue anyway" on the shield (counted as a slip); hard lock has none and a
  10 minute pause budget; "Only these websites" shields every other site.

## Not done

- **Scribe**: the drawing board is not on the phone. It is a canvas with layers, pen smoothing and images; it needs
  its own build, not a quick port.
- **Finishing a review on the phone**: marking it solved, scoring it and rescheduling it. The desktop's scheduling
  and score rules would have to be copied exactly, and a mistake would shift every later review date. Time it here,
  finish it on the computer.
- **Live updates without polling**: the phone checks the cloud every 5 to 10 seconds while open, not a live connection.

## Limits that come from iOS (cannot be fixed in the app)

- The system will not run a monitoring window shorter than 15 minutes. A block shorter than that is lifted by the app
  itself at its end; if the app is killed in that gap the shield can last until 15 minutes are up.
- A person can turn Screen Time access off in iOS Settings at any time. Carmen cannot stop that. It notices the next
  time it runs, shows a warning and counts a slip on the session.
- Carmen never sees which apps the person picked; Apple's picker hands back tokens only.

## Build it (on a Mac)

1. Install Xcode 15+ and XcodeGen: `brew install xcodegen`.
2. `cp Config.example.xcconfig Config.xcconfig`, then fill in your team id and the two Supabase values (the same ones
   the desktop has in `private/.env`).
3. `xcodegen generate`, then open `CarmenFocus.xcodeproj`.
4. Run the core tests: `cd CarmenCore && swift test`.
5. Fix whatever the compiler finds, then run on a **real device**. Screen Time shielding does not work in the simulator.

## Apple approval (start this first)

Family Controls needs an entitlement Apple grants per app. Request it for the app and for each extension at
<https://developer.apple.com/contact/request/family-controls-distribution>. Development builds work on your own
devices without it; TestFlight and the App Store need it approved. Approval can take days to weeks.

In the developer portal also create the App Group `group.app.carmenfocus` and the bundle ids
`app.carmenfocus.ios`, `.monitor`, `.shieldconfig`, `.shieldaction`, `.report`, each with Family Controls and that App Group.
