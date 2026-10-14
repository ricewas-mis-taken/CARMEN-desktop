# Mac handoff: sync test + Mac port

For the Claude Code session on the MacBook. Two jobs, in this order:
1. Test cross-computer sync (Mac = machine B, a Windows PC = machine A).
2. Make Carmen Focus run on macOS.

Read first: `CLAUDE.md`, `FEATURE_IMPLEMENT.txt`, `DESIGN_DECISIONS.txt`, `sync_server/README.md`.
Commit style: one change per commit, `type: short lowercase`. Open a PR per job, don't merge.

## 0. One-time setup (the human does this)

- [ ] Supabase SQL editor (project `rbkwtcjaqsmjbimtqeng`): run `sync_server/migrations/001_direct_sync.sql`, then `002_realtime.sql`.
- [ ] Mac: clone the repo, `python3 -m venv .venv`, install requirements (skip the Windows-only ones, see part 2).
- [ ] Sign in with the same account on both machines. Wait about 10 s.

## 1. Cross-computer sync test

A = Windows PC, B = Mac. Tick each box only after seeing it with your own eyes. Write any failure with the exact numbers (seconds of lag, what showed).

- [ ] Start a 1-minute session on A. B shows the same session and timer within 5 s.
- [ ] Pause on A: B pauses. Resume on B: A resumes. Timers match to the second.
- [ ] Add a blocked app / allowed domain on A mid-session: B enforces it.
- [ ] Session lasting about 3 s on A, then ended: B ends cleanly, no leftover timer.
- [ ] That 3 s session appears once in Session History on B (no duplicates).
- [ ] Review started with no task on A shows on B.
- [ ] Screen Time: each machine shows the other's device tab.
- [ ] Saved default block/allow list edited on A shows on B.
- [ ] QuickNote written on A shows on B.
- [ ] B offline, change on A, B back online: B catches up within about 30 s.
- [ ] Sign out and back in: stays signed in across restarts ("save account forever").

If sync reports "needs one-time setup", the SQL in part 0 has not been run.

## 2. Mac port

Windows-only pieces (found by grepping for win32/winreg/pywin32/ctypes.windll):

| File | What it does on Windows | Mac job |
|---|---|---|
| `requirements.txt` | `pywin32`, `winsdk` | make them Windows-only (`; sys_platform == "win32"`) |
| `window_tracker.py`, `enforcer.py` | find/minimise/close blocked app windows | replace with macOS APIs (pyobjc / AppKit; needs Accessibility permission) |
| `qt_ui/enforcer_overlay.py` | full-screen overlay | check it works on macOS |
| `taskbar_quiet.py` | hide taskbar badges / flashing | Dock badge equivalent, or skip on Mac |
| `autostart.py` | start with Windows | LaunchAgent plist |
| `desktop_menu.py`, `quicknote_launcher.py` | desktop right-click "QuickNote" (registry) | Mac: Services / menu-bar item instead |
| `singleinstance.py` | one app at a time | lock file or socket |
| `tray.py` | `pystray` tray icon | menu-bar icon; pystray works on macOS but must run on the main thread, check against Qt |
| `calendar_toast.py` | Windows toasts | `osascript` notification or pyobjc |
| `installed_apps.py` | app picker list | scan `/Applications` |
| `sync_realtime.py` | idle detection via `GetLastInputInfo` | `Quartz.CGEventSourceSecondsSinceLastEventType` |
| `main.py` | startup wiring | guard each Windows call with `sys.platform` |
| `auto_updater.py` | git pull + restart | check the restart path (`os.execv`) on macOS |

Rules:
- Wrap platform code behind small modules with the same function names, and branch on `sys.platform`. Don't scatter `if` checks.
- Windows behaviour must not change. Run `python -m pytest -q tests` on the Mac, and fix or skip (with a reason) anything Windows-specific.
- Block lists store process names like `chrome.exe`. Decide how Mac app names map (store the bundle id or the executable name) and sync it, so a block on one computer applies on the other. Write the decision in `DESIGN_DECISIONS.txt`, with a note in the extension repo's copy too (`carmen-extension`).
- The browser extension talks to `http://127.0.0.1:5847`. Make sure the API starts on Mac and the token copy item works.

Done when: the app launches on the Mac, a session blocks an app and a site, the extension pairs, and part 1 passes with the Mac as B.
