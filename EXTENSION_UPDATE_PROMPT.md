# Prompt: update the Chrome extension to report violation resolution

Paste this into your extension-side session to drive the change.

---

I need to update the Focus Tracker Chrome extension's `background.js` to
support violation *resolution* tracking on the desktop app's local server
(`http://127.0.0.1:5847`), not just violation *reporting*.

## Current behavior

The extension already does this, and it stays exactly as-is:

```
POST /violation
Content-Type: application/json
Body: { "url": "<the offending url>" }
```

— called every time the active tab's domain isn't in `domain_whitelist`
during an active session.

## What needs to change

The desktop server now tracks, per violation, how long it took before the
user got back on track — but for domain violations it can only know that if
the extension tells it. It cannot observe tab changes itself. So:

Add a new call:

```
POST /violation/resolved
Content-Type: application/json
Body: { "type": "domain" }
```

**When to call it:** whenever the active tab's domain transitions from
"not in `domain_whitelist`" to "in `domain_whitelist`" during an active
session — i.e. the exact inverse of the condition that currently triggers
`POST /violation`. Practically, this means wherever the extension's
tab/URL-change listener currently branches on "is this domain
whitelisted?", add the `else` (or equivalent "now compliant") branch to call
`POST /violation/resolved` instead of doing nothing.

**Rules:**
- Only call it if a violation could plausibly be open — i.e. only within an
  active session (same guard the existing `POST /violation` call already
  uses). No harm if called with nothing actually open server-side (it's a
  documented no-op), but there's no need to call it when the session isn't
  active.
- It's fine to call it on every qualifying tab/URL change, even if you're not
  sure a violation was open — the server handles the "nothing to resolve"
  case gracefully (still returns 200).
- Don't call it merely because the tab *closed* or the browser lost focus —
  only because the *active* tab is now on an allowed domain. If the user
  switches away from the browser entirely (e.g. to a whitelisted desktop
  app), that's the desktop app's own process-tracking that handles it, not
  this endpoint.
- No response body needs special handling — it returns the current session
  status shape, but the extension doesn't need to do anything with it beyond
  checking for a 2xx.

## Example

If your current listener looks roughly like:

```js
function onActiveTabChanged(url) {
  if (!isActiveSessionRunning()) return;
  if (!isUrlWhitelisted(url)) {
    reportViolation(url); // existing POST /violation call
  }
}
```

it should become:

```js
function onActiveTabChanged(url) {
  if (!isActiveSessionRunning()) return;
  if (!isUrlWhitelisted(url)) {
    reportViolation(url); // POST /violation (unchanged)
  } else {
    reportViolationResolved(); // NEW: POST /violation/resolved, body { type: "domain" }
  }
}
```

Please locate the actual tab/URL-change handling in `background.js`, and
implement `reportViolationResolved()` alongside the existing
`reportViolation()` following whatever fetch/error-handling pattern
`reportViolation()` already uses (same base URL, same fire-and-forget
tolerance for the desktop app not running).
