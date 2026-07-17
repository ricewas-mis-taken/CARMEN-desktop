# Carmen Focus Desktop

A Windows desktop companion that tracks the active foreground application during a
focus session and enforces a whitelist of allowed apps — the whole-computer
counterpart to the Carmen browser extension, which does the same thing for browser
tabs. It runs as a single standalone process: a local REST API, a window-polling
loop, and a system tray icon all run concurrently in one program, with no manual
server-starting required by the user.

This app's local API is the single shared source of truth for focus session state.
The browser extension reads and writes the *same* session through this API instead
of tracking its own separate state — session state holds both a `processWhitelist`
(apps, checked by this app) and a `domainWhitelist` (sites, checked by the
extension), so one session covers both.

This module is designed to run as an independent process that the larger Carmen
system (a personal voice/AI assistant that routes queries to different backends)
calls into over the local HTTP API documented below. It does not depend on Carmen
directly, so it can be developed, tested, and eventually packaged on its own.

## Running for testing

```
pip install -r requirements.txt
python main.py
```

This starts the Flask API on `127.0.0.1:5847`, begins polling the active window
every 1.5 seconds, and shows a tray icon. No session is enforced until you start
one — either via the tray's "Start Focus Session" dialog, or `POST /session/start`
directly (curl, the browser extension, or eventually Carmen's voice interface).
The tray menu also lets you pick your whitelist, check status, view past sessions,
pause/resume the countdown, end a session immediately, or quit the app. The
"Pause Session"/"Resume Session" and "End Session (Nuclear)" items only appear
in the menu while a session is actually active — pause/resume each log a
timestamped entry in that session's log, visible in the "Session History"
viewer alongside violations and whitelist additions.

Every completed session (ended manually or by running out the clock) is appended
to `session_history.json` — see "Session history" below.

## API

The server binds to `127.0.0.1` only — it is not reachable from other machines.
CORS is enabled for all origins (including `chrome-extension://...`) since this is
a localhost-only API anyway, so the browser extension can call it directly.

### `GET /health`

Check whether the service is running.

```
curl http://127.0.0.1:5847/health
```

```json
{"ok": true}
```

### `GET /status`

Current session status.

```
curl http://127.0.0.1:5847/status
```

```json
{
  "isActive": true,
  "isPaused": false,
  "secondsRemaining": 1423,
  "lockMode": "soft",
  "processWhitelist": ["Code.exe", "chrome.exe"],
  "domainWhitelist": ["github.com", "docs.google.com"],
  "violationCount": 2,
  "violationLog": [
    {
      "kind": "process", "process": "chrome.exe",
      "timestamp": "2026-07-14T10:03:12.001", "lockMode": "soft",
      "resolvedAt": "2026-07-14T10:03:47.221", "durationSeconds": 35
    },
    {
      "kind": "domain", "url": "https://reddit.com/r/funny",
      "timestamp": "2026-07-14T10:11:47.512", "lockMode": "soft",
      "resolvedAt": null, "durationSeconds": null
    }
  ],
  "lastAcceptableProcess": "Code.exe"
}
```

`domainWhitelist` is stored here purely as shared state — this app never reads it;
it's the browser extension's job to fetch it from `GET /status` and match it
against active tabs.

`isPaused` reflects whether the countdown is currently frozen (see
`POST /session/pause` below) — the session stays `isActive: true` and lock
enforcement keeps running exactly as normal while paused; only
`secondsRemaining` stops moving.

Each `violationLog` entry is `kind: "process"` (this app's own window-polling,
identified by `"process"`) or `kind: "domain"` (reported by the browser
extension via `POST /violation`, identified by `"url"`). `resolvedAt`/
`durationSeconds` are `null` until the violation is resolved — for `"process"`
entries that happens automatically the moment the foreground app is back on
`processWhitelist`; for `"domain"` entries the extension has to explicitly call
`POST /violation/resolved` (see below) since this app has no way to observe tab
changes itself. If a violation is still open when the session ends, it stays
`null` forever — "never corrected before the session ended."

The same log also carries `kind: "pause"` / `kind: "resume"` entries (just
`{"kind": ..., "timestamp": ...}`, no resolution fields) whenever
`POST /session/pause` / `POST /session/resume` is called, so session
history shows breaks inline with violations in chronological order.

### `POST /session/start`

Starts a new focus session, replacing any existing one.

```
curl -X POST http://127.0.0.1:5847/session/start \
  -H "Content-Type: application/json" \
  -d '{
    "duration_minutes": 25,
    "lock_mode": "soft",
    "process_whitelist": ["Code.exe", "chrome.exe"],
    "domain_whitelist": ["github.com", "docs.google.com"]
  }'
```

```json
{
  "isActive": true,
  "secondsRemaining": 1500,
  "lockMode": "soft",
  "processWhitelist": ["Code.exe", "chrome.exe"],
  "domainWhitelist": ["github.com", "docs.google.com"],
  "violationCount": 0,
  "lastAcceptableProcess": null
}
```

`lock_mode` must be `"soft"` or `"hard"`. `process_whitelist` is a list of process
names (e.g. `"chrome.exe"`), matched case-insensitively and exactly, and is what
this app's own window-polling loop checks. `domain_whitelist` is a list of
domain/URL substrings (e.g. `"github.com"`) for the browser extension to match
against active tabs — this app stores it but doesn't act on it itself.

`domain_whitelist` must always be a list. `process_whitelist` may be `null` or
omitted entirely (the browser extension does this, since it doesn't collect an
app whitelist) — in that case the session starts with whatever process whitelist
was last saved via the tray's "Pick Apps to Whitelist" picker
(`config.json`'s `processWhitelist`), rather than being rejected or reset to empty.

### `POST /session/pause`

Freezes the countdown only — the session stays `isActive: true` and lock mode
(soft/hard) keeps being enforced exactly as before. Doesn't touch violation
tracking, whitelists, or lock mode at all. No body. Idempotent: if no session
is active, or the session is already paused, just returns the current status
unchanged.

```
curl -X POST http://127.0.0.1:5847/session/pause
```

Returns the current `GET /status` shape, with `isPaused: true` and
`secondsRemaining` frozen at whatever it was the moment pause was called —
every subsequent `GET /status` poll returns that same frozen number until
resumed. Appends a `{"kind": "pause", "timestamp": ...}` entry to
`violationLog`.

### `POST /session/resume`

Resumes the countdown from exactly the `secondsRemaining` it was frozen at —
the pause duration never counts against the timer. No body. Idempotent: if no
session is active, or the session isn't paused, just returns the current
status unchanged.

```
curl -X POST http://127.0.0.1:5847/session/resume
```

Returns the current `GET /status` shape, with `isPaused: false` and
`secondsRemaining` ticking down again. Appends a
`{"kind": "resume", "timestamp": ...}` entry to `violationLog`.

Both endpoints, and `isPaused`/the frozen `secondsRemaining`, are persisted to
`session_state.json` on every call, so an app restart while paused doesn't
silently resume the countdown.

### `POST /violation`

Called by the browser extension whenever the active tab's domain isn't in
`domain_whitelist` during an active session. Increments the same
`violationCount`/`violationLog` that `GET /status` returns, opening a new
`kind: "domain"` entry with `resolvedAt`/`durationSeconds` both `null` until
resolved.

```
curl -X POST http://127.0.0.1:5847/violation \
  -H "Content-Type: application/json" \
  -d '{"url": "https://reddit.com/r/funny"}'
```

```json
{"violationCount": 4}
```

### `POST /violation/resolved`

Called by the browser extension when the active tab is back on an allowed
domain. Closes out the most recent open `kind: "domain"` violation (if any),
filling in `resolvedAt` and `durationSeconds` — this is what makes "how long
before returning to correct" possible to compute for domain violations. A
no-op (not an error) if there's nothing open.

```
curl -X POST http://127.0.0.1:5847/violation/resolved \
  -H "Content-Type: application/json" \
  -d '{"type": "domain"}'
```

Returns the current `GET /status` shape. `type` is currently always `"domain"` —
process-side resolution already happens automatically via this app's own
window-polling loop, so there's nothing else to resolve today.

### `POST /session/end`

Ends the current session immediately (the tray's "End Session (Nuclear)" option
calls this same logic directly, not over HTTP).

```
curl -X POST http://127.0.0.1:5847/session/end
```

```json
{
  "isActive": false,
  "secondsRemaining": 0,
  "lockMode": "soft",
  "processWhitelist": [],
  "domainWhitelist": [],
  "violationCount": 3,
  "violationLog": [
    {"process": "chrome.exe", "timestamp": "2026-07-14T10:03:12.001", "lockMode": "soft"},
    {"url": "https://reddit.com/r/funny", "timestamp": "2026-07-14T10:11:47.512", "lockMode": "soft"},
    {"process": "chrome.exe", "timestamp": "2026-07-14T10:20:02.884", "lockMode": "soft"}
  ],
  "lastAcceptableProcess": null
}
```

The response reflects the session that was just ended (its final violation count
and full log), not the freshly-reset state — this is what the tray's "End Session"
option summarizes in its notification (e.g. "3 violation(s): chrome.exe x2,
Discord.exe x1") so you can see how much you strayed once the session is over.

### `GET /apps/running`

Lists currently running apps with a visible window, one entry per unique process
name (deduplicated — e.g. several Chrome windows only show up once).

```
curl http://127.0.0.1:5847/apps/running
```

```json
[
  {"process_name": "chrome.exe", "window_title": "New Tab - Google Chrome"},
  {"process_name": "Code.exe", "window_title": "main.py - carmen-focus-desktop"},
  {"process_name": "Discord.exe", "window_title": "Discord"}
]
```

### `GET /apps/installed`

Lists installed apps (not just currently running ones) by scanning Start Menu
shortcuts for both all-users and the current user, resolving each `.lnk` to its
target `.exe`, and deduplicating by process name. Apps that are always-allowed
(see below — Settings, Explorer, Windows Terminal, etc.) are left out, since
they're never a meaningful whitelist choice. This is what the whitelist picker
(see below) is built on.

```
curl http://127.0.0.1:5847/apps/installed
```

```json
[
  {"process_name": "Code.exe", "display_name": "Visual Studio Code"},
  {"process_name": "chrome.exe", "display_name": "Google Chrome"},
  {"process_name": "Discord.exe", "display_name": "Discord"}
]
```

### `POST /whitelist/apps`

Saves a list of process names to `config.json` as the default `processWhitelist`
for future sessions. This does **not** start or modify an active session — it's
just persisting a default so you don't have to retype it into `POST
/session/start` every time.

```
curl -X POST http://127.0.0.1:5847/whitelist/apps \
  -H "Content-Type: application/json" \
  -d '{"process_whitelist": ["Code.exe", "chrome.exe"]}'
```

```json
{"processWhitelist": ["Code.exe", "chrome.exe"]}
```

### `POST /whitelist/apps/add`

Adds a single process to the *active* session's `processWhitelist`, with a
required `reason` logged to `processWhitelistAdditions` for the audit trail —
the mid-session counterpart to `POST /whitelist/apps` above, which only
touches the saved default and doesn't require a reason. This is also what the
lock overlay's own "Whitelist" button calls internally (via
`session_manager.add_process_to_whitelist`) when you whitelist an app straight
from a redirect popup. 400s if no session is active.

```
curl -X POST http://127.0.0.1:5847/whitelist/apps/add \
  -H "Content-Type: application/json" \
  -d '{"process_name": "Discord.exe", "reason": "coordinating with team"}'
```

```json
{
  "processWhitelist": ["Code.exe", "Discord.exe"],
  "addition": {
    "process": "Discord.exe",
    "reason": "coordinating with team",
    "timestamp": "2026-07-16T14:02:11.000"
  }
}
```

### `GET /history`

Every completed session, oldest first — start/end time, lock mode, the
process/domain whitelists in effect, and the full `violationLog` (same shape as
`GET /status`'s, including `resolvedAt`/`durationSeconds`). Same data the tray's
"Session History" viewer renders.

```
curl http://127.0.0.1:5847/history
```

```json
[
  {
    "startTime": "2026-07-14T09:00:00.000",
    "endTime": "2026-07-14T09:30:00.000",
    "lockMode": "soft",
    "processWhitelist": ["Code.exe"],
    "domainWhitelist": ["github.com"],
    "violationCount": 1,
    "violationLog": [
      {
        "kind": "domain", "url": "https://reddit.com",
        "timestamp": "2026-07-14T09:05:00.000", "lockMode": "soft",
        "resolvedAt": "2026-07-14T09:05:40.000", "durationSeconds": 40
      }
    ]
  }
]
```

A session is only recorded here once it actually ends — manually (tray "End
Session", `POST /session/end`) or by running out the clock (checked lazily,
the next time `GET /status` is polled after `secondsRemaining` hits 0). Calling
`POST /session/end`/`end_session()` when no session is running is a no-op and
does **not** create a phantom entry.

## Whitelist picker & start-session dialog (tray GUI)

Picking a whitelist and starting a session both happen through native Tkinter
windows (`picker_gui.py`), not a web page — no browser round-trip:

- Tray → **"Pick Apps to Whitelist"** opens a scrollable checkbox list built from
  `installed_apps.list_installed_apps()` (same data as `GET /apps/installed`).
  Apps already in the saved `processWhitelist` come back pre-checked, so
  re-opening the picker to tweak your list doesn't lose previous picks. "Save
  Whitelist" writes the checked set straight to `config.json`.
  - When there's no active session, the top of the list also shows two
    quick-pick sections pulled from `session_history`'s most recent entry:
    **"Added mid-session last time"** — specifically the apps that were let
    in via the reason-required flow (tray picker or the lock overlay's own
    "Whitelist" button) last session, each labeled with the reason that was
    given — and **"From your last session"** — the rest of that session's
    full `processWhitelist`. Both let you re-check an app without hunting for
    it in the installed-apps scan below (which can miss anything without a
    Start Menu shortcut). They reflect whichever session most recently
    completed, so they naturally change after the next one ends.
  - A manual "Not listed? Add by name or file" row lets you type an exe name
    directly (e.g. `xxx.exe`) or click **Browse...** to pick the executable
    through a file dialog — either way it's added as a new checked row using
    just the filename, since that's what enforcement matches processes on.
- Tray → **"Start Focus Session"** opens a small dialog for duration (minutes)
  and lock mode (soft/hard). "Start Session" calls the exact same
  `session_manager.start_session()` that `POST /session/start` calls — it's the
  same shared session state, so the browser extension sees the new session (via
  its own `GET /status` polling) the moment it starts, with no extra wiring.

Both `GET /apps/installed` and `POST /whitelist/apps` stay available over HTTP
too, for driving the same picks programmatically (e.g. from Carmen).

## Session history (tray GUI)

Tray → **"Session History"** opens a native Tkinter window (`history_gui.py`)
listing every completed session, newest first, reading the same
`session_history.json` that `GET /history` returns. Each session is rendered as
a block:

```
2026-07-14 09:00:00  →  2026-07-14 09:30:00   (30m 0s, soft lock)
──────────────────────────────────────────────────────────────────
Allowed apps:  Code.exe
Allowed sites: github.com
Violations: 2
  [domain] https://reddit.com  —  09:05:00  —  back on track after 40s
  [domain] https://youtube.com  —  09:22:10  —  never corrected before session ended

────────────────────────────────────────────────────────────────────
```

with a full-width line separating each session and a shorter one under each
header — resolved violations are shown in green, unresolved ones in red. `GET
/history` returns the identical underlying data for anything that wants to
render it differently (e.g. Carmen).

## Lock modes

- **soft**: a small always-on-top, borderless popup appears for 5 seconds when you
  switch to a non-whitelisted app, showing a green progress bar filling up and a
  live, ticking countdown of session time remaining. It repeatedly re-lifts and
  refocuses itself so it's hard to ignore, but does **not** take a system-wide
  input grab — it only competes for foreground attention with the offending
  window, and never freezes or interrupts any other app or background exe. No
  window action is taken against the offending app.
- **hard**: the offending foreground window is minimized (unless it's exempt —
  see below), then if a window for the last acceptable app is currently open,
  it's brought back to the foreground — only restoring it from a minimized
  state if it was actually minimized, so a window that's snapped/half-screen
  isn't resized in the process. Note: some lightweight/background apps
  (widget-style trackers, minimal utility tools) have been observed to mishandle
  a forced minimize and quit outright instead of actually minimizing — this
  previously caused a background WPM tracker to get closed, which led to
  minimize being removed for a time. It's since been restored, on the
  understanding that this same class of fragile app could in principle hit the
  issue again; the minimize call is wrapped in try/except and skipped entirely
  for exempt/system processes, which is the extent of the safety net. If no
  acceptable window is open, nothing happens except the reminder below.
  Immediately after, an overlay appears for 3 seconds ("Redirected from X —
  back to Y"), then closes on its own.

Enforcement only triggers on a *change* to a new non-whitelisted foreground app,
not on every poll tick while you stay on the same one.

Both the soft and hard overlays include a **"Whitelist"** button whenever the
offending process name is known. Clicking it opens a small dialog that still
requires a reason (same as `POST /whitelist/apps/add` above) before adding the
app to `processWhitelist` for the rest of the session — it doesn't touch lock
mode or violation tracking, it just lets that one app through going forward.
The addition is logged to `processWhitelistAdditions` and shows up in the
session history viewer the same way mid-session whitelist picks already do.

### Always-allowed system processes

Regardless of the session whitelist, a fixed set of core Windows shell/system
processes is never treated as a violation: `explorer.exe`, `ShellExperienceHost.exe`,
`SearchHost.exe`/`SearchApp.exe`, `StartMenuExperienceHost.exe`,
`ApplicationFrameHost.exe`, `TextInputHost.exe`, `LockApp.exe`, `dwm.exe`,
`sihost.exe`, `Widgets.exe`/`WidgetBoard.exe`, all the Settings surfaces
(`SystemSettings.exe`, `SystemSettingsBroker.exe`, legacy `control.exe`),
`Taskmgr.exe`, and Windows Terminal (`WindowsTerminal.exe`, `wt.exe`,
`OpenConsole.exe`) — this is what makes alt-tab, the taskbar, the
wifi/time/notification flyouts, Start menu/search, Task Manager, Settings, and
the terminal work normally during a session instead of getting fought over. This
app's own process (the tray icon and the popups above) is exempt the same way, by
PID, so its own windows never self-trigger a violation. This list lives in
`session_manager.ALWAYS_ALLOWED_PROCESSES`, and the whitelist picker filters it
out of `GET /apps/installed` entirely — these apps are never offered as
whitelist choices since checking or unchecking them wouldn't do anything.

Minimizing `explorer.exe` specifically was found to sometimes destabilize it and
visually shrink unrelated snapped windows — since it's now always-allowed, hard
lock will never touch it. Note that Windows' own snap-assist behavior (resizing a
sibling snapped window when another one is minimized) is an OS-level feature
outside this app's control, and can still occur if the *offending* app itself is
snapped next to something else.

`ALWAYS_ALLOWED_PROCESSES` also covers vendor/GPU overlays (NVIDIA App and its
helper processes, `GPUView.exe`) and Git for Windows' own bundled launchers
(Git Bash/CMD/GUI) — none of these are meaningful things to guard focus against.

### Where installed apps come from

`installed_apps.list_installed_apps()` (used by both the Tkinter picker and
`GET /apps/installed`) combines two discovery sources so the picker isn't
limited to whatever happens to be running:

- **Start Menu `.lnk` shortcuts** (all-users + current-user) — covers
  traditional installers (Squirrel/Electron, NSIS, MSI, etc).
- **Installed MSIX/Store packages** — apps like Spotify and Claude for Desktop
  ship as packages and never create a Start Menu `.lnk` shortcut at all, so the
  shortcut scan alone silently misses them entirely. This source shells out to
  PowerShell to run `Get-AppxPackage` (filtered to non-framework, non-system
  packages) joined against `Get-StartApps` for the human-readable display name
  — `Get-StartApps` already resolves the manifest's `ms-resource:...`
  indirection the same way the real Start menu does, so this module doesn't
  have to reimplement PRI resource lookup itself. Each package's manifest-
  declared launch target isn't trusted directly (e.g. Spotify's manifest points
  at `SpotifyMigrator.exe`, but the process that actually runs day to day is
  `Spotify.exe`) — instead the install folder is scanned directly for `.exe`
  files, filtered to strip obvious helper/updater/crash-reporter executables,
  and scored against the display name to pick the most likely real app exe.

Both sources feed the same deduplicated-by-process-name result.

Beyond the always-allowed process list, two more filters are applied so the
picker only shows real, pickable apps:

- **Native Windows utility folders** — Start Menu shortcuts filed under
  `Accessibility`, `Accessories`, `Administrative Tools`/`Windows Tools`,
  `Windows Ease of Access`, `Windows PowerShell`, `Maintenance`, `System Tools`,
  or `Startup` are skipped wholesale (folder pruned during the scan), rather than
  trying to name every individual Character Map/Disk Cleanup/Steps Recorder-style
  utility one by one.
- **Generic installer/uninstaller executables** — `setup.exe`, `install.exe`,
  `installer.exe`, `msiexec.exe`, and Inno Setup's `unins000.exe`-style
  uninstallers are filtered regardless of which app's folder they're found in;
  they only run for a few seconds during an install and are never something
  you'd focus-switch to.

It also unwraps Squirrel.Windows-style shortcuts (used by Discord, Spotify,
Slack, and many other Electron apps), whose Start Menu shortcut actually points
at a shared `Update.exe` stub with the real app named in
`--processStart <App>.exe` inside the shortcut's arguments — without this, all
of these apps would collapse into one meaningless `Update.exe` entry that could
never actually match during enforcement (the updater only runs for a moment
before handing off to the real, differently-named process).

## Future packaging

This app is meant to be packaged into a standalone `.exe` for end users via
PyInstaller:

```
pyinstaller --onefile --windowed main.py
```

That packaging step is not done yet — this repo currently runs via `python main.py`
for development and testing.
