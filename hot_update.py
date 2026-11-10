"""Puts new code into the running app without closing it.

Only code that holds no live state is swapped: the pages, dialogs and pure logic listed in SAFE. A page is
rebuilt in place, on the same tab, so nothing is closed and nothing has to be reopened. Code that holds a
running session, threads, the API or the tray (everything not in SAFE) cannot be swapped under a running
app; it is reported as "later" and takes effect the next time Carmen starts on its own.

A reloaded class or function is a new object, so names other modules imported with `from x import y` are
pointed at the new ones too (rebind)."""
import importlib
import inspect
import os
import sys

from calendar_log import logger

PURE = ["vacation", "pause_budget", "unblock_gate", "week_plan", "goal_raise", "report_card"]
# Modules that hold live data (the running session, the stores, the tracker's counters). They are swapped too, with
# their data carried over: the new code is loaded into the same module, then every piece of live data (dicts, locks,
# loaded flags) is put back, new keys of a dict filled in. The module's own lock is held meanwhile, so no thread sees
# the data half-reset. A loop already running keeps its old body until the next start, but everything it calls is new.
STATEFUL = ["review_scheduler", "session_history", "score_store", "tasks_store", "goal_notify", "board_store",
            "devices_store", "quicknote_store", "scribe_store", "session_manager", "review_store", "enforcer",
            "window_tracker", "sync_client"]
# Small always-open windows: closed and opened again on the new code if they were showing.
WINDOWS = {"qt_ui.quicknote": ("open_quicknote", "_win"), "qt_ui.mini_timer": ("open_mini_timer", "_win")}
DIALOGS = ["qt_ui.explainer", "qt_ui.pause_reason", "qt_ui.pause_label", "qt_ui.week_dialog", "qt_ui.report_card_dialog", "qt_ui.options_dialog",
           "qt_ui.tour_content", "qt_ui.tour"]
PAGES = {                      # page key -> (module, class)
    "home": ("qt_ui.home_tab", "HomeTab"),
    "focus": ("qt_ui.focus_tab", "FocusTab"),
    "tasks": ("qt_ui.tasks_tab", "TasksTab"),
    "board": ("qt_ui.board_tab", "BoardTab"),
    "review": ("qt_ui.review_tab", "ReviewTab"),
    "screentime": ("qt_ui.screentime_tab", "ScreenTimeTab"),
    "history": ("qt_ui.history_viewer", "HistoryTab"),
    "openings": ("qt_ui.openings_tab", "OpeningsTab"),
    "scribe": ("qt_ui.scribe_tab", "ScribeTab"),      # sits in a scroll area; saved before it is replaced
}
SAFE = PURE + STATEFUL + DIALOGS + [m for m, _ in PAGES.values()] + list(WINDOWS)   # reload order: dependencies first


_NOT_THE_APP = ("tests", "scripts", "mac-os", "sync_server", "archive", "design-skills", ".wt")


def module_for(path):
    """'qt_ui/home_tab.py' -> 'qt_ui.home_tab'; None for anything that is not a python module."""
    if not path.endswith(".py"):
        return None
    return path[:-3].replace("\\", "/").replace("/", ".")


def _defined_here(module):
    return {name: obj for name, obj in vars(module).items()
            if (inspect.isclass(obj) or inspect.isfunction(obj)) and getattr(obj, "__module__", None) == module.__name__}


def _is_data(name, value):
    """A module-level name that holds live data rather than code or a constant."""
    if name.startswith("__") or name.isupper() or name.strip("_").isupper():
        return False
    return not (inspect.ismodule(value) or inspect.isclass(value) or inspect.isroutine(value))


def _reload_keeping_data(old):
    lock = getattr(old, "_lock", None)
    held = False
    if lock is not None and hasattr(lock, "acquire"):
        held = lock.acquire(timeout=3)
        if not held:
            raise RuntimeError("busy: " + old.__name__)
    kept = {n: v for n, v in vars(old).items() if _is_data(n, v)}
    try:
        importlib.reload(old)
        for name, value in kept.items():
            fresh = vars(old).get(name)
            if isinstance(value, dict) and isinstance(fresh, dict):
                for key, default in fresh.items():
                    value.setdefault(key, default)          # a key the new code added gets its default
            setattr(old, name, value)
    finally:
        if held:
            try:
                lock.release()
            except RuntimeError:
                pass


def reload_module(name):
    """Reloads one module and points every other module's reference to its old classes / functions at the new."""
    old = sys.modules[name]
    before = _defined_here(old)
    if name in STATEFUL:
        _reload_keeping_data(old)
    else:
        importlib.reload(old)
    after = _defined_here(old)
    swap = {id(obj): after[n] for n, obj in before.items() if n in after and after[n] is not obj}
    if not swap:
        return
    for other in list(sys.modules.values()):
        if other is old or other is None:
            continue
        try:
            items = list(vars(other).items())
        except TypeError:
            continue
        for key, value in items:
            replacement = swap.get(id(value))
            if replacement is not None:
                try:
                    setattr(other, key, replacement)
                except (AttributeError, TypeError):
                    pass


def _tour_running():
    try:
        from qt_ui import tour_runner
        return bool(tour_runner._tour is not None and tour_runner._tour.active)
    except Exception:
        return False


def swap(files):
    """Applies the changed files to the running app. Returns {"live": [modules swapped], "pages": [rebuilt],
    "later": [files that need the next start]}."""
    live, later, pages = [], [], []
    wanted = set()
    for path in files:
        path = path.replace("\\", "/")
        if path.split("/")[0] in _NOT_THE_APP:
            continue
        if path.endswith(".qss"):
            later.append(path)
            continue
        module = module_for(path)
        if module is None:
            continue
        if module in SAFE and module in sys.modules and not (module.startswith("qt_ui.tour") and _tour_running()):
            wanted.add(module)
        elif module in SAFE:
            continue                                   # never imported in this run: nothing live to update
        else:
            later.append(path)
    for name in SAFE:
        if name in wanted:
            try:
                reload_module(name)
            except Exception:
                logger.exception("hot_update: could not swap %s", name)
                later.append(name.replace(".", "/") + ".py")
                continue
            live.append(name)
    if live:
        pages = _rebuild_pages(set(live)) + _reopen_windows(set(live))
    return {"live": live, "pages": pages, "later": later}


def _reopen_windows(reloaded):
    """QuickNote and the mini timer: whichever was showing is closed (saving as it does) and shown again, new code."""
    reopened = []
    for name, (opener, attribute) in WINDOWS.items():
        if name not in reloaded:
            continue
        module = sys.modules[name]
        window = getattr(module, attribute, None)
        if window is None:
            continue
        showing = window.isVisible()
        try:
            window.close()
            window.deleteLater()
        except Exception:
            logger.exception("hot_update: could not close %s", name)
        setattr(module, attribute, None)
        if showing:
            getattr(module, opener)()
            reopened.append(name.rsplit(".", 1)[-1])
    return reopened


def _rebuild_pages(reloaded):
    import qt_ui.main_window as main_window
    window = main_window._win
    if window is None:
        return []
    rebuilt = []
    for key, (module, cls_name) in PAGES.items():
        if module not in reloaded or key not in window._pages:
            continue
        cls = getattr(sys.modules[module], cls_name)
        old = window._pages[key]
        index = window._stack.indexOf(old)
        was_current = window._stack.currentWidget() is old
        kwargs = {"on_navigate": window._show_tab} if key == "home" else {}
        try:
            if key == "scribe":
                from qt_ui.main_window import _scrolling
                inner = old.widget() if hasattr(old, "widget") else old
                if hasattr(inner, "flush"):
                    inner.flush()                      # the board on screen is written out first
                new = _scrolling(cls())
            else:
                new = cls(**kwargs)
        except Exception:
            logger.exception("hot_update: could not rebuild the %s page", key)
            continue
        window._stack.insertWidget(index, new)
        window._stack.removeWidget(old)
        window._pages[key] = new
        if was_current:
            window._stack.setCurrentWidget(new)
        old.hide()
        old.deleteLater()
        rebuilt.append(key)
    return rebuilt
