# Linked guides (family features)

A parent or an instructor can **link** to a student's Carmen and then set site rules, start and end focus
sessions, add recurring study blocks and send notes. The student sees who is linked and exactly what each
person can see, and can end any link in one click. This is the desktop half; the browser extension has its own.

**Off by default.** Nothing here runs, and nothing leaves the computer, until the student signs in to a family
account. With it off the app behaves exactly as before (tests prove it: `tests/test_family_hooks.py`).

## Setup (Windows)

1. Start the family backend (today: the CarmenLinked dev backend, `npm start`, default `http://localhost:8420`).
   Later this is the Supabase project (see "Backends" below).
2. In Carmen: sidebar **Options** (or the account menu) -> **Linked guides...** -> sign in with the family
   account's email and password. The **Server address** box defaults to `http://localhost:8420`; change it only
   if you were given another address. It must be `https://` unless it is on this computer.
3. A parent or instructor links to you with a code (they issue one, you redeem it in the family web app, or you
   issue one for them). You see them appear under **Who is linked** within about 15 seconds, with a note.
4. To stop: **End link** (two steps: a question, then the action) removes one guide at once. **Sign out** stops
   every guide's rules and sessions on this computer.

Your sign-in is kept in the Windows Credential Manager (service `CarmenDesktop`, entry `family_refresh_token`),
like the sync account's. Everything else it remembers is `private/family_state.json` (this computer only).

## What a guide can do here

| Guide action | What Carmen does |
|---|---|
| Start a session (lock mode, allowed sites, minutes) | Starts a normal Carmen session: same lock modes, same window tracker, same popups. Sites come from the guide; the apps blocked are your own saved app list. Labelled **Set by <name>** on the lock popup and in the Focus tab. You can end it early; the guide sees that. You cannot add sites to it yourself (you ask them), because that would undo their rules. |
| End the session | Ends only a session that guide started. Never your own. |
| Set rules (allow / deny sites) | Applied during sessions, **deny first**: a denied site stays blocked even if allowed elsewhere. Applied live to a running guide session. |
| Study block (weekday, start-end, time zone, lock, sites) | Starts a session when the block begins and ends it when cover ends (back-to-back blocks are one session). If several overlap the lock is hard if any is hard and the sites are joined. Ending a block early skips it for that day (never restarts), and the guide sees the skip. Your own blocks work too (labelled "Your own study block"). A block never interrupts a session you started yourself. |
| Send a link / message | A note you can read (and a desktop notice). Opening a pushed link allows its site in a guide's running session. |
| Class windows (instructors) | Shown as "a class is on". A class window only exposes a deny-only soft rule to the extension; it never starts a session by itself. |

Nothing a guide does is silent: every command, rules change, started session and link change leaves a note under
**Notes from your guides** and a desktop notice. Declined commands (expired, malformed) are acknowledged as
declined and noted quietly.

## What the desktop shares

Only what at least one **active** link's scopes allow, hostname only, and only from the moment of consent
(history from before a guide linked is never sent later):

* a device check-in every ~15 s (device id, "desktop", enforcing, whether the rules are fresh, whether a session runs) -- the only thing sent with no link, so a guide can see "link lost";
* live status (idle / live / paused / break, lock, start, end, pause minutes, violation count) -- `status` scope;
* each finished session (when, length, lock, how it ended, violation count, pause counts) -- `sessions`/`integrity`; task name only with `tasks`; a review is only ever called "Review";
* detail-level links additionally get the reason you gave for ending, and the **hostnames** of blocked sites and unblock plans;
* website time (hostname + seconds + the minute) -- `screentime`. App names are never sent.

Never sent: screenshots, keystrokes, page titles, page content, full URLs, app names, incognito anything. The panel
shows an **Activity shared** log of what this computer sent (with values) next to what the server recorded, and
**What guides did** (the server's append-only audit of every guide action about you).

## Offline, and bad data

* Offline keeps enforcing: the last good rules, the last good study schedule and any running session carry on. We never drop a rule because we could not hear about it, and never invent one.
* A reply that is not exactly the expected shape counts as "could not reach": one malformed block rejects the whole schedule and the cached copy stays (so a hard lock can never turn soft). A malformed command is declined, never half-applied.
* A guide's session ends when its link is gone (seen from the server), when you end it, when its clock runs out, or when you sign out. If the server cannot be reached nothing is ended early.
* Access tokens last 1 h; the client refreshes before they run out (single-flight, refresh token in the credential store). A refresh sends this device's id so a crash between "server spent the token" and "saved the new one" is survivable.

## The local API for the browser extension

`GET /family/rules` (open like `/status`, loopback only, read-only). With the features off it returns
`{"enabled": false, "allow": [], "deny": [], "byGuide": [], "version": "", "status": "off", "studyBlock": null, "classWindow": null}`.
When on: `allow`/`deny` (merged; **apply deny first**), `byGuide[{guideId,name,allow,deny,note}]`, `version`
(changes when rules change), `status` (`ok` heard from the server in the last 5 min, `stale` cached/offline,
`none`), `studyBlock` (active block: `{lockMode, allow, endsAt, by}`) and `classWindow`
(`{name, by, endsAt, deny}`, deny-only, soft). On an internal error it answers **503**, which a client must treat
as "unreachable, keep what you have", never as "no rules". `GET /status` gains `guide` (null, or who set the
running session: `{name, kind, ...}`) so the extension can say "Set by <name>"; old extensions ignore it.
`POST /whitelist/domains/add` answers 403 during a guide's session.

**Extension parity:** the browser extension (`CARMEN-chrome-extension`, branch `desktop-guide-rules`) now polls `/family/rules`; see its README and DESIGN_DECISIONS.

## Backends

`family_transport.py` is the interface. Two implementations, chosen by the config key `familyBackend` (`dev` by default,
or `supabase`) in `family_sync.make_transport()`, and by the **Family server** choice in Options -> Linked guides -> sign-in:

* `family_rest.py` talks to the CarmenLinked dev backend (`familyApiBase`, default `http://localhost:8420`).
* `family_supabase.py` talks to the real Supabase project (`familySupabaseUrl`, default `https://wwkoiwegmgweblyaxmdw.supabase.co`;
  `familySupabaseKey`, the public publishable key, prefilled). It is a stdlib-only port of the CarmenLinked JS adapter
  (`carmenlinked/client/src`): GoTrue password grant / refresh / logout, then PostgREST with **the student's own token**, so Row Level
  Security decides what is visible. Reads and writes are the SQL functions of `carmenlinked/supabase/*.sql` (`my_rules`, `my_commands`,
  `ack_command`, `me_heartbeat`, `me_push_status`, `me_push_summaries`, `me_push_screentime`, `my_schedule_json`, `revoke_link_json`, ...),
  which answer in the dev backend's JSON, so `family_sync.py`, the store and the panel did not change. Status and summaries are stored
  by the server as the desktop's own `sync_records` shapes (`live_session` / `session_history`) with a strictly increasing aware-UTC
  `updated_at`; the client sends the shapes and the server owns the clock. Sign-up is never called; no auth setting is touched.
  The refresh token is kept in the Credential Manager under its own entry (`family_refresh_token_supabase`), refresh is single-flight,
  https is required off-loopback, and "could not reach" (network, 5xx, bad JSON) is kept apart from "refused" (the server said no).
  The schema check test (`tests_family/test_family_supabase_stub.py`) confirms every function it calls exists in the SQL.

Differences you will notice with Supabase: no `ETag` (RPC calls are POSTs); the refresh token is tolerated for re-use for about 10 s
only (the dev backend allows 60 s from the same device), so a crash between "server spent the token" and "saved the new one" is
survivable only when the app restarts within that window; an access token already issued stays valid for up to 1 h after a sign-out
(the refresh token is revoked at once). A new email must be confirmed before it can sign in.

Tests: `tests_family/test_family_supabase.py` (fake Supabase on a local port; always runs), and
`tests_family/test_family_supabase_live.py` (marker `supabase`; skipped unless `CARMENLINKED_DIR` is set; uses throwaway users created
by `tests_family/tools/ct_bridge.mjs` through the carmenlinked harness; run it under `flock /tmp/ct-supabase.lock`).

## Known limits

* Windows-only behaviour (lock popups, window blocking, toasts, Credential Manager) was not run on Windows by the author; see the test notes in the hand-off.
* "Planned minutes" for a session you started yourself is approximated by its wall-clock length (the app does not store the original plan).
* Per-violation timeline events (`/me/violations`) are not sent; guides see the violation count and, at detail level, hostnames in the session summary.
* Quiet hours (a student-controlled switch on the server) need no desktop code: the server hides status from guides.
* A study block cannot reach the last minute of a day (`23:59` end is exclusive, blocks never cross midnight; same as the backend).
* A guide's allow list cannot express "except this subdomain"; the deny list is exposed to the extension for that.
* Assignments, kudos, goals, requests ("ask for more time") are not in the desktop UI yet (kudos arrive as server notes only).
* The guide never sets which apps are blocked; apps blocked are the student's saved list.

## Checklist walk-through (FEATURE_IMPLEMENT.txt)

1. Desktop core: `family_rules.py` (pure), `family_sync.py`, `family_report.py`, `family_store.py`, `family_host.py`; `session_manager` gains `guide` (default `None`, cleaned, in `_DEFAULT_STATE`, cleaned in `_clean_remote_session`, filed in the history entry only when set). Tests use the isolation fixtures; `family_state.json` is declared local-only in `tests/test_sync_coverage.py`.
2. Local API: `GET /family/rules` (new, read-only, safe "off" default), `GET /status.guide` (null default). No mutating route added, so no `_require_token`.
3. Qt UI: Options -> Linked guides (`qt_ui/family_panel.py`), "Set by" on the lock popup (`qt_ui/enforcer_overlay.py`) and in the Focus tab's session label. A running guide session is edited live by `update_session`.
4-6. Extension (chrome/ and firefox/): done in CARMEN-chrome-extension (branch desktop-guide-rules).
7. Sync: `guide` travels with the running session (shared on purpose: the other computer also shows who set it); `config` keys `familyEnabled`/`familyApiBase` are per computer (not in `SHARED_SETTINGS`); the family cache is local-only.
8. Design decision logged in `DESIGN_DECISIONS.txt` (2026-11-24); counterpart note to be added in the extension repos.
