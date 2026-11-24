"""Options -> Linked guides: the one place a student sees and controls everything a parent or instructor can do.

What it always shows, whatever the guides do (this is the point of the feature, not a nicety):
  * who is linked, and in plain words exactly what each of them can see and do;
  * a button to end any link, in two steps (a question, then the action), taking effect at once;
  * what is set for you right now (a guide's session, a study block, rules) and who set it;
  * every note a guide's action produced, and the log of what this computer shared.

Nothing here talks to the network on the window's thread: sign-in, ending a link and refreshing the logs run in a
helper thread and hand the answer back (same pattern as account_widget). The panel reads family_sync's snapshot,
which is a local read, so redrawing is cheap; sections are only rebuilt when their data changed so a half-typed
password is never wiped by a refresh.
"""
import json
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import family_rules
from qt_ui.ui_kit import (
    AMBER, DANGER_STYLE, GREEN, GREY, SECONDARY_STYLE, accent, chip, dialog_title, field_label, note, role_label,
)

REFRESH_MS = 3000
_CARD_STYLE = "QFrame#FamilyCard{background:#FFFFFF;border:1px solid #E7EAF0;border-radius:14px;}"


def _when(ms):
    if not ms:
        return ""
    moment = datetime.fromtimestamp(ms / 1000.0)
    hour = moment.hour % 12 or 12
    return f"{moment:%a} {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


def _when_iso(text):
    ms = family_rules.parse_iso_ms(text)
    return _when(ms) if ms else ""


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            widget = item.widget()
            widget.hide()                    # gone from the screen now, not when the event loop gets to deleteLater
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear(item.layout())


def _card():
    frame = QFrame()
    frame.setObjectName("FamilyCard")
    frame.setStyleSheet(_CARD_STYLE)
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 14, 16, 14)
    box.setSpacing(6)
    return frame, box


def _thread_runner(work, done):
    """Runs work() off the window's thread, then done(result) back on it."""
    import qt_gui_thread

    def run():
        try:
            result = work()
        except Exception as exc:                      # never leave the panel waiting forever
            result = exc
        qt_gui_thread.run_on_gui_thread(lambda: done(result))
    threading.Thread(target=run, daemon=True).start()


class FamilyPanel(QWidget):
    """`sync` is a family_sync.FamilySync (tests pass a fake); `runner(work, done)` runs work off-thread."""

    def __init__(self, sync=None, runner=None, auto_refresh=True):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        if sync is None:
            import family_sync
            sync = family_sync.instance()
        self._sync = sync
        self._run = runner or _thread_runner
        self._sigs = {}
        self._confirming = None            # the link id whose "end link" question is open
        self._busy = False
        self.setObjectName("PopupBg")
        self.setWindowTitle("Linked guides")
        self.resize(620, 780)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 16)
        outer.setSpacing(8)
        outer.addWidget(dialog_title("Linked guides"))
        outer.addWidget(role_label(
            "A parent or instructor can link to you. You see who is linked and what they can see, "
            "and you can end any link in one click.", "subtitle", wrap=True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        body = QWidget()
        body.setObjectName("FamilyBody")
        body.setStyleSheet("QWidget#FamilyBody{background:transparent;}")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 6, 8, 6)
        self._body.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._sections = {}
        for key in ("account", "guides", "now", "notes", "shared", "audit"):
            holder = QVBoxLayout()
            holder.setSpacing(6)
            self._sections[key] = holder
            self._body.addLayout(holder)
        self._body.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        done = accent(QPushButton("Done"))
        done.clicked.connect(self.close)
        row.addWidget(done)
        outer.addLayout(row)

        self.refresh(force=True)
        if auto_refresh:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self._timer.start(REFRESH_MS)
            self._pull_logs()

    # ---- redrawing ----
    def _redo(self, key, data, build):
        """Rebuild one section only if what it shows changed."""
        sig = json.dumps(data, sort_keys=True, default=str)
        if self._sigs.get(key) == sig:
            return
        self._sigs[key] = sig
        layout = self._sections[key]
        _clear(layout)
        build(layout)

    def refresh(self, force=False):
        if force:
            self._sigs.clear()
        snap = self._sync.snapshot()
        self._redo("account", {"e": snap["enabled"], "s": snap["signedIn"], "u": snap["user"], "b": snap["base"],
                               "r": snap["reachable"], "busy": self._busy}, lambda l: self._build_account(l, snap))
        self._redo("guides", {"l": snap["links"], "c": self._confirming}, lambda l: self._build_guides(l, snap))
        self._redo("now", {"r": snap["rules"], "l": snap["links"]}, lambda l: self._build_now(l, snap))
        self._redo("notes", snap["notices"], lambda l: self._build_notes(l, snap))
        self._redo("shared", {"m": (snap["mirror"] or {}).get("sharedLog"), "s": snap["sentLog"][:30]}, lambda l: self._build_shared(l, snap))
        self._redo("audit", (snap["mirror"] or {}).get("audit"), lambda l: self._build_audit(l, snap))

    # ---- account ----
    def _build_account(self, layout, snap):
        layout.addWidget(field_label("Family account"))
        card, box = _card()
        if snap["signedIn"]:
            user = snap["user"] or {}
            box.addWidget(role_label(f"Signed in as {user.get('email') or user.get('displayName') or 'your family account'}", "subtitle"))
            if snap["reachable"]:
                status = chip("Connected", tone=GREEN)
                text = "Guides can reach you, and you can reach them."
            else:
                status = chip("Offline", tone=AMBER)
                text = "Can't reach the server. Carmen keeps using the rules it last received, and nothing is dropped."
            top = QHBoxLayout()
            top.addWidget(status)
            top.addStretch(1)
            box.addLayout(top)
            box.addWidget(note(text))
            out = QPushButton("Sign out")
            out.setStyleSheet(SECONDARY_STYLE)
            out.setFixedHeight(34)
            out.clicked.connect(self._sign_out)
            row = QHBoxLayout()
            row.addWidget(out)
            row.addStretch(1)
            box.addLayout(row)
            box.addWidget(note("Signing out stops every guide's rules and sessions on this computer right away."))
        else:
            box.addWidget(note("Sign in to a family account to link with a parent or instructor. "
                               "Nothing is shared until someone links to you, and only what is listed under their name."))
            box.addWidget(field_label("Email"))
            self._email = QLineEdit()
            self._email.setPlaceholderText("you@example.com")
            box.addWidget(self._email)
            box.addWidget(field_label("Password"))
            self._password = QLineEdit()
            self._password.setEchoMode(QLineEdit.Password)
            box.addWidget(self._password)
            box.addWidget(field_label("Server address"))
            self._base = QLineEdit(snap["base"] or "")
            self._base.setPlaceholderText("https://...")
            box.addWidget(self._base)
            box.addWidget(note("Only change this if you were given a different address. It must be https:// unless it is on this computer."))
            self._message = QLabel("")
            self._message.setWordWrap(True)
            self._message.setVisible(False)
            box.addWidget(self._message)
            self._sign_in_button = accent(QPushButton("Sign in"))
            self._sign_in_button.setMinimumHeight(38)
            self._sign_in_button.clicked.connect(self._sign_in)
            self._password.returnPressed.connect(self._sign_in)
            box.addWidget(self._sign_in_button)
        layout.addWidget(card)

    def _say(self, text, error=True):
        label = getattr(self, "_message", None)
        if label is None:
            return
        try:
            label.setStyleSheet(f"color:{'#B42318' if error else '#1B2230'};font-size:12.5px;")
            label.setText(text)
            label.setVisible(bool(text))
        except RuntimeError:
            pass                                # the section was rebuilt while the call was running

    def _sign_in(self):
        if self._busy:
            return
        email, password, base = self._email.text().strip(), self._password.text(), self._base.text().strip()
        if not email or not password:
            self._say("Enter your email and password.")
            return
        self._busy = True
        self._sign_in_button.setEnabled(False)
        self._say("Signing in...", error=False)
        self._run(lambda: self._sync.sign_in(email, password, base or None), self._after_sign_in)

    def _after_sign_in(self, result):
        self._busy = False
        ok, message = (False, "Sign-in failed. Please try again.") if isinstance(result, Exception) else result
        if ok:
            self._pull_logs()
            self.refresh(force=True)
        else:
            self._say(message or "Sign-in failed. Please try again.")
            try:
                self._sign_in_button.setEnabled(True)
            except RuntimeError:
                pass

    def _sign_out(self):
        self._run(lambda: self._sync.sign_out(), lambda _r: self.refresh(force=True))

    # ---- guides ----
    def _build_guides(self, layout, snap):
        layout.addWidget(field_label("Who is linked"))
        active = [l for l in snap["links"] if l["active"]]
        if not active:
            layout.addWidget(note("No one is linked to you. Nothing is shared."))
            return
        for link in active:
            card, box = _card()
            rel = "instructor" if link["relation"] == "instructor" else "parent"
            head = QHBoxLayout()
            name = QLabel(link["guideName"])
            name.setStyleSheet("font-size:15px;font-weight:700;background:transparent;")
            head.addWidget(name)
            head.addWidget(chip(rel.capitalize(), tone=GREY))
            head.addStretch(1)
            box.addLayout(head)
            box.addWidget(role_label("They can see", "field"))
            for line in link["sees"]:
                box.addWidget(note("• " + line, size=12.5))
            if link["can"]:
                box.addWidget(role_label("They can", "field"))
                for line in link["can"]:
                    box.addWidget(note("• " + line, size=12.5))
            else:
                box.addWidget(note("They cannot set rules or start sessions on your computer."))
            box.addWidget(note("They never see your screen, what you type, page titles or full web addresses. "
                               "Website names only.", size=12))
            self._add_end_link(box, link)
            layout.addWidget(card)

    def _add_end_link(self, box, link):
        if self._confirming == link["id"]:
            box.addWidget(note(f"End the link with {link['guideName']}? They stop seeing anything and their rules stop applying right now. "
                               "You can link again later with a new code.", color="#B42318"))
            row = QHBoxLayout()
            yes = QPushButton(f"Yes, end the link with {link['guideName']}")
            yes.setStyleSheet(DANGER_STYLE)
            yes.setFixedHeight(34)
            yes.setObjectName("confirmEndLink")
            yes.clicked.connect(lambda _c=False, i=link["id"]: self._end_link(i))
            keep = QPushButton("Keep it")
            keep.setStyleSheet(SECONDARY_STYLE)
            keep.setFixedHeight(34)
            keep.clicked.connect(self._cancel_end)
            row.addWidget(yes)
            row.addWidget(keep)
            row.addStretch(1)
            box.addLayout(row)
        else:
            end = QPushButton("End link")
            end.setStyleSheet(SECONDARY_STYLE)
            end.setFixedHeight(34)
            end.setObjectName("endLink")
            end.clicked.connect(lambda _c=False, i=link["id"]: self._ask_end(i))
            row = QHBoxLayout()
            row.addWidget(end)
            row.addStretch(1)
            box.addLayout(row)

    def _ask_end(self, link_id):
        self._confirming = link_id
        self.refresh()

    def _cancel_end(self):
        self._confirming = None
        self.refresh()

    def _end_link(self, link_id):
        self._confirming = None
        self._run(lambda: self._sync.revoke_link(link_id), self._after_end)

    def _after_end(self, result):
        if isinstance(result, tuple) and not result[0]:
            self._flash(result[1] or "Couldn't end the link. Try again.")
        self.refresh(force=True)

    def _flash(self, text):
        layout = self._sections["now"]
        label = note(text, color="#B42318")
        layout.insertWidget(0, label)
        QTimer.singleShot(8000, label.deleteLater)

    # ---- what is set right now ----
    def _build_now(self, layout, snap):
        layout.addWidget(field_label("Set for you right now"))
        rules = snap["rules"]
        lines = []
        by = self._sync.session_attribution() if hasattr(self._sync, "session_attribution") else ""
        if by:
            lines.append(f"{by}: a session is running.")
        if rules.get("studyBlock"):
            blk = rules["studyBlock"]
            lines.append(f"Study block ({', '.join(blk['by'])}): {blk['lockMode']} lock, {len(blk['allow'])} allowed site"
                         f"{'' if len(blk['allow']) == 1 else 's'}, until {_when_iso(blk['endsAt'])}.")
        if rules.get("classWindow"):
            win = rules["classWindow"]
            lines.append(f"{win['name']} is on ({win['by']}), until {_when_iso(win['endsAt'])}.")
        for guide in rules.get("byGuide") or []:
            if guide["allow"] or guide["deny"]:
                bits = ([f"allows {len(guide['allow'])} site{'' if len(guide['allow']) == 1 else 's'}"] if guide["allow"] else []) + \
                       ([f"blocks {len(guide['deny'])}"] if guide["deny"] else [])
                extra = f" Note: {guide['note']}" if guide.get("note") else ""
                lines.append(f"{guide['name']} {' and '.join(bits)} during sessions.{extra}")
        if rules.get("enabled") and rules.get("status") == "stale":
            lines.append("These are the rules last received; the server can't be reached right now.")
        if not lines:
            layout.addWidget(note("Nothing is set for you right now."))
            return
        card, box = _card()
        for line in lines:
            box.addWidget(note(line, size=13))
        layout.addWidget(card)

    # ---- notes ----
    def _build_notes(self, layout, snap):
        head = QHBoxLayout()
        head.addWidget(field_label("Notes from your guides"))
        head.addStretch(1)
        shown = [n for n in snap["notices"] if not n.get("silent")]
        if any(not n["read"] for n in shown):
            mark = QPushButton("Mark all as read")
            mark.setStyleSheet(SECONDARY_STYLE)
            mark.setFixedHeight(28)
            mark.clicked.connect(self._mark_read)
            head.addWidget(mark)
        layout.addLayout(head)
        if not shown:
            layout.addWidget(note("No notes yet. Everything a guide does shows up here."))
            return
        for n in shown[:30]:
            card, box = _card()
            weight = "700" if not n["read"] else "400"
            line = QLabel(n["text"])
            line.setWordWrap(True)
            line.setStyleSheet(f"font-size:13px;font-weight:{weight};background:transparent;")
            box.addWidget(line)
            box.addWidget(note(f"{_when(n['atMs'])}" + (f"  ·  {n['by']}" if n.get("by") else ""), size=11.5))
            layout.addWidget(card)

    def _mark_read(self):
        self._sync.store.mark_notices_read()
        self.refresh(force=True)

    # ---- logs ----
    def _build_shared(self, layout, snap):
        layout.addWidget(field_label("Activity shared"))
        layout.addWidget(note("What this computer sent to your guides, and what the server recorded. Website names only; "
                              "a guide only receives what their link allows.", size=12))
        rows = []
        for entry in snap["sentLog"][:25]:
            items = entry.get("items") or []
            what = {"heartbeat": "Device check-in", "status": "Live status", "summaries": "Finished sessions",
                    "screentime": "Website time"}.get(entry["kind"], entry["kind"])
            detail = ""
            if entry["kind"] == "screentime" and items:
                detail = ", ".join(sorted({i.get("hostname", "") for i in items if isinstance(i, dict)})[:6])
            elif entry["kind"] == "summaries" and items:
                detail = ", ".join(f"{i.get('focusedMin', 0)} min" for i in items[:4] if isinstance(i, dict))
            elif entry["kind"] == "status" and items and isinstance(items[0], dict):
                detail = f"{items[0].get('state', '')}"
            rows.append(f"{_when(entry['atMs'])}  ·  {what}" + (f" x{entry['count']}" if entry["count"] > 1 else "") + (f"  ({detail})" if detail else ""))
        server = (snap["mirror"] or {}).get("sharedLog") or []
        for s in reversed(server[-8:]):
            rows.append(f"Server recorded {_when_iso(s['at'])}: {s['kind']} x{s['count']} (fields: {', '.join(s['fields'][:8])})")
        if not rows:
            layout.addWidget(note("Nothing has been shared yet."))
            return
        card, box = _card()
        for row in rows[:30]:
            box.addWidget(note(row, size=12))
        layout.addWidget(card)

    def _build_audit(self, layout, snap):
        layout.addWidget(field_label("What guides did"))
        audit = (snap["mirror"] or {}).get("audit") or []
        if not audit:
            layout.addWidget(note("Nothing yet. Every action a guide takes about you is listed here, and it cannot be removed."))
            return
        card, box = _card()
        for a in reversed(audit[-20:]):
            box.addWidget(note(f"{_when_iso(a['at'])}  ·  {a['detail'] or a['action']}", size=12))
        layout.addWidget(card)

    def _pull_logs(self):
        if not getattr(self._sync, "enabled", lambda: False)():
            return
        self._run(lambda: self._sync.refresh_mirror(), lambda _r: self.refresh())


_popups = set()


def open_panel(sync=None):
    """Opens the panel as its own window and keeps a reference so it is not collected while shown."""
    panel = FamilyPanel(sync)
    _popups.add(panel)
    panel.destroyed.connect(lambda: _popups.discard(panel))
    panel.show()
    return panel
