"""Small reusable scrollable-checklist widget: a Canvas+Scrollbar of
Checkbuttons plus a manual "add by name" row. Factored out of picker_gui.py's
whitelist picker so the event editor's per-event process whitelist (see
calendar_gui.py) can reuse the exact same UI pattern instead of re-deriving
it, without touching picker_gui.py's already-tested tray-picker flow."""
import tkinter as tk


def build_checklist(parent, items, checked_keys, key_fn=None, label_fn=None):
    """items: list of arbitrary values (e.g. app dicts or plain strings).
    checked_keys: set of keys (lowercased) that should start checked.
    key_fn(item) -> str used for de-dup/checked-matching (defaults to str(item).lower()).
    label_fn(item) -> str shown next to the checkbox (defaults to str(item)).

    Returns (container_frame, vars_by_key, add_row_fn) where vars_by_key is a
    dict of key -> tk.BooleanVar (live — read it after the caller's own
    "Save"/"OK" action), and add_row_fn(key, label, checked) lets the caller
    append more rows later (e.g. a manually typed entry)."""
    key_fn = key_fn or (lambda item: str(item).lower())
    label_fn = label_fn or (lambda item: str(item))
    checked_keys = {k.lower() for k in checked_keys}

    container = tk.Frame(parent)
    canvas = tk.Canvas(container, highlightthickness=0, height=180)
    scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    list_frame = tk.Frame(canvas)

    list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=list_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    vars_by_key = {}

    def add_row(key, label, checked):
        key_lower = key.lower()
        if key_lower in vars_by_key:
            return
        var = tk.BooleanVar(master=parent, value=checked)
        tk.Checkbutton(list_frame, text=label, variable=var, anchor="w", justify="left").pack(
            fill="x", anchor="w"
        )
        vars_by_key[key_lower] = (var, key)

    for item in items:
        key = key_fn(item)
        add_row(key, label_fn(item), checked=key.lower() in checked_keys)

    return container, vars_by_key, add_row


def get_checked(vars_by_key):
    """Returns the original (non-lowercased) keys whose checkbox is checked."""
    return [key for var, key in vars_by_key.values() if var.get()]
