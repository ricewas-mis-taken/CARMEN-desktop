"""Which computers use this account, and which browser extensions are linked to Carmen on each.

An extension links to the Carmen on its own computer (it holds that computer's key), so every computer
records its own links and the rows sync like tasks do: any computer can then list all of them.
A link is seen whenever a request carrying the key arrives; "connected" means seen in the last few minutes."""
import json
import os
import platform
import threading
import time
from datetime import datetime, timezone

import device_id
import paths

PATH = paths.private_path("devices.json")
CONNECTED_SECONDS = 180          # seen this recently = connected now
WRITE_EVERY_SECONDS = 120        # "last seen" is saved at most this often, so it does not sync every request
_lock = threading.Lock()
_last_write = {}


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(include_deleted=False):
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rows = [d for d in data.get("devices", []) if isinstance(d, dict) and d.get("id")] if isinstance(data, dict) else []
    return rows if include_deleted else [d for d in rows if not d.get("isDeleted")]


def save(rows):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"devices": rows}, f, indent=2)
    os.replace(tmp, PATH)


def browser_from(user_agent, origin=""):
    ua = user_agent or ""
    if origin.startswith("moz-extension") or "Firefox/" in ua:
        return "Firefox"
    if "Edg/" in ua:
        return "Edge"
    if "OPR/" in ua:
        return "Opera"
    if "Chrome/" in ua:
        return "Chrome"
    return "Browser"


def _account():
    try:
        import auth_manager
        user = auth_manager.get_current_user()
        return (user or {}).get("email") or ""
    except Exception:
        return ""


def note_extension(origin, user_agent):
    """A request carrying this computer's key arrived from an extension: remember it."""
    if not origin or "-extension://" not in origin:
        return False
    ext_id = origin.split("://", 1)[1].strip("/")
    browser = browser_from(user_agent, origin)
    now = time.time()
    with _lock:
        key = (ext_id, browser)
        if now - _last_write.get(key, 0) < WRITE_EVERY_SECONDS:
            return False
        _last_write[key] = now
        me = device_id.get_device_id()
        rows = load(include_deleted=True)
        row = next((d for d in rows if d["id"] == me), None)
        if row is None:
            row = {"id": me, "extensions": []}
            rows.append(row)
        link = next((e for e in row["extensions"] if e.get("extensionId") == ext_id and e.get("browser") == browser), None)
        if link is None:
            link = {"extensionId": ext_id, "browser": browser, "firstSeen": _now_iso()}
            row["extensions"].append(link)
        link["lastSeen"] = _now_iso()
        row.update(computer=platform.node(), account=_account(), updatedAt=_now_iso(), deviceId=me, isDeleted=False)
        save(rows)
    try:
        import sync_trigger
        sync_trigger.note_change()
    except Exception:
        pass
    return True


def _age_seconds(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def summary():
    """[{computer, account, this_pc, links: [{browser, last_seen_seconds, connected}]}], this computer first."""
    me = device_id.get_device_id()
    out = []
    for row in load():
        links = [{"browser": e.get("browser", "Browser"), "extensionId": e.get("extensionId", ""),
                  "seen_seconds": _age_seconds(e.get("lastSeen")),
                  "connected": _age_seconds(e.get("lastSeen")) <= CONNECTED_SECONDS}
                 for e in row.get("extensions", [])]
        out.append({"computer": row.get("computer") or "A computer", "account": row.get("account") or "",
                    "this_pc": row["id"] == me, "links": links})
    out.sort(key=lambda d: (not d["this_pc"], d["computer"]))
    return out


def totals():
    """(computers with at least one linked extension, links in all, links connected right now)"""
    rows = summary()
    pcs = [r for r in rows if r["links"]]
    return len(pcs), sum(len(r["links"]) for r in pcs), sum(1 for r in pcs for l in r["links"] if l["connected"])
