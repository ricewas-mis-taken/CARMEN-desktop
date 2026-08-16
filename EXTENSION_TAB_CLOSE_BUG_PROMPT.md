# Prompt: fix press-and-hold tab close sometimes closing the whole window

Paste this into your extension-side session to drive the fix.

---

## Bug report

The Focus Tracker Chrome extension's press-and-hold-on-a-tab-to-close
gesture has a bug: after it's been triggered a handful of times in the same
browser session, it stops closing just the tab and starts closing the
*entire window* instead (every tab in it). It should always close only the
one tab that was held.

## Where to look

Find wherever `background.js` (or a content/injected script, if the
press-and-hold UI is rendered into the page rather than as a browser-action
popup) implements this gesture — likely a `mousedown`/`pointerdown` timer
that, once held long enough, calls something like `chrome.tabs.remove(tabId)`.

## Likely root cause to check first

A stale or wrong id ending up passed to a `chrome.tabs.remove(...)` /
`chrome.windows.remove(...)` call, most plausibly:

- The held tab's id is captured once (e.g. on `mousedown`) and reused later
  on a timer/`mouseup`, but by the time the hold completes the *tab* has
  already been closed or its id reused by Chrome for a different tab —
  if the code has any fallback like "if `tabs.remove` fails / id not
  found, remove the window instead," that fallback firing is exactly this
  bug.
- The gesture handler tracks state (e.g. `heldTabId`, `holdTimer`) in a
  variable that isn't correctly reset after each use — a leftover/stale id
  from a *previous* held-and-closed tab could resolve to `undefined` or to
  a window id instead of a tab id on a later call, especially if tab ids
  and window ids are ever compared/used interchangeably anywhere nearby.
- Double-firing: if the hold timer isn't cleared on the first trigger and
  fires again, a second `chrome.tabs.remove` call on an id that's already
  gone could be caught by a catch block that escalates to closing the
  window as a "just get rid of it somehow" fallback.

## What "fixed" looks like

- Holding on a tab always closes exactly that one tab, every time, no
  matter how many times the gesture has already been used in the same
  browser session.
- No code path in this gesture should ever call `chrome.windows.remove(...)`
  — only `chrome.tabs.remove(tabId)`, with the correct, freshly-read tab id
  for the tab actually being held. If a fallback-to-closing-the-window
  exists for error handling, remove it entirely rather than papering over
  when it fires — it should never be a valid outcome of this gesture.
- Any hold-timer/tracked-tab-id state should be fully reset after every use
  (success or failure), not just on the happy path.

Please locate the actual press-and-hold handler, diagnose which of the
above (or something else) is causing the escalation to `windows.remove`,
and fix it so only the held tab is ever closed.
