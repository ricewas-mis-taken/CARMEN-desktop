# Prompt: show "Until burnout" (not a countdown) for burnout sessions

Paste this into your extension-side session to drive the change.

---

## Bug report

The desktop app has (had) a matching bug: starting a task with "Until I
burnout" runs an unbounded session under the hood by giving it a very long
internal duration ceiling (8 hours). Every timer display in the desktop app
was showing that as a normal countdown -- "7h 58m remaining" -- instead of
making clear there's no real deadline. This has just been fixed on the
desktop side; the same fix needs to land in the extension.

## What changed on the desktop/server side

`GET /status` (`http://127.0.0.1:5847/status`) now returns an `isBurnout`
boolean alongside the existing fields (`isActive`, `isPaused`,
`secondsRemaining`, `startTime`, `violationLog`, etc.):

```
GET /status
200 OK
{
  "isActive": true,
  "isPaused": false,
  "startTime": "2026-08-20T09:15:00.123456",
  "secondsRemaining": 27845,
  "violationLog": [...],
  "isBurnout": true,
  ...
}
```

`isBurnout` is `true` only for a session actually started as "Until I
burnout" -- never for a normal timed session, and never for a review
session (reviews already have their own separate "no fixed duration, show
elapsed instead" treatment and should stay exactly as they are; this is
additive, not a replacement for that).

## What to change in the extension

Wherever the extension currently renders `secondsRemaining` as a countdown
(popup UI, badge text, any injected page overlay showing time left) --
locate that logic and branch on `isBurnout`:

- **`isBurnout: true`** -- show "Until burnout" as the label, with elapsed
  time computed underneath/alongside it instead of a countdown. Elapsed
  time should be pause-aware: compute it from `startTime` and the
  pause/resume entries in `violationLog` (each entry has `"kind": "pause"`
  or `"kind": "resume"` with a `"timestamp"`), the same way the desktop
  does it -- sum the wall-clock spans between consecutive pause/resume
  events (and from the last resume/start to now), skipping any interval
  where the session was paused. Do NOT just use `now - startTime` raw, or
  time spent paused gets wrongly counted as elapsed.
- **`isBurnout: false`** (the existing/default case) -- unchanged, keep
  showing `secondsRemaining` as a countdown exactly as today.

## Example

If the popup's status renderer currently looks roughly like:

```js
function renderTimer(status) {
  const { minutes, seconds } = toMinutesSeconds(status.secondsRemaining);
  timerLabel.textContent = `${minutes}m ${seconds}s remaining`;
}
```

it should become something like:

```js
function renderTimer(status) {
  if (status.isBurnout) {
    const elapsed = pauseAwareElapsedSeconds(status.startTime, status.violationLog);
    const { minutes, seconds } = toMinutesSeconds(elapsed);
    timerLabel.textContent = `Until burnout — ${minutes}m ${seconds}s elapsed`;
    return;
  }
  const { minutes, seconds } = toMinutesSeconds(status.secondsRemaining);
  timerLabel.textContent = `${minutes}m ${seconds}s remaining`;
}
```

Please locate every place the extension renders a countdown from
`secondsRemaining` (popup, badge, any in-page overlay) and apply this same
`isBurnout` branch consistently across all of them, matching how the
desktop app now shows the same session identically everywhere (Tasks tab,
Focus tab, and the system tray tooltip).
