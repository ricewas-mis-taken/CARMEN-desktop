"""Unified Qt scrollable-checklist component, replacing checklist_widget.py
(Tk) *and* retiring picker_dialogs.py's Stage-1 duplicate ad-hoc
implementation (_ScrollableChecklist) -- both existed for the same reason
the original checklist_widget.py did (factored out so more than one caller
can share it), but picker_gui.py's Tk version never actually adopted the
shared module it was extracted from. This is the single implementation
both the blocklist picker and the event editor's two checklists
(process/domain blocklist) use from Stage 5 on.

Same signature shape as the original Tk build_checklist()/get_checked(),
deliberately preserved to minimize call-site churn: build the rows once,
read checked state on demand (after the caller's own Save action) rather
than treating the returned dict as a point-in-time snapshot.
"""
from PySide6.QtWidgets import QCheckBox, QLabel, QScrollArea, QVBoxLayout, QWidget


def build_checklist(items, checked_keys, key_fn=None, label_fn=None, height=180):
    """items: list of arbitrary values (e.g. app dicts or plain strings).
    checked_keys: iterable of keys (case-insensitive) that should start
    checked. key_fn(item) -> str for de-dup/checked-matching (defaults to
    str(item).lower()). label_fn(item) -> str shown next to the checkbox
    (defaults to str(item)).

    Returns (container, checkboxes_by_key, add_row_fn) where container is a
    QScrollArea the caller inserts into its own layout, checkboxes_by_key is
    a live {key_lower: (QCheckBox, original_key)} dict (read it after the
    caller's own Save/OK action -- see get_checked()), and
    add_row_fn(key, label, checked) lets the caller append more rows later
    (e.g. a manually typed entry, or a previously-saved value the initial
    `items` scan didn't surface)."""
    key_fn = key_fn or (lambda item: str(item).lower())
    label_fn = label_fn or (lambda item: str(item))
    checked_keys = {k.lower() for k in checked_keys}

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFixedHeight(height)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setStyleSheet("background: transparent;")
    content = QWidget()
    content.setObjectName("PopupBg")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.addStretch(1)
    scroll.setWidget(content)

    checkboxes_by_key = {}

    def _apply_checked_style(checkbox, is_checked):
        checkbox.setStyleSheet("color: #2e7d32; font-weight: 600;" if is_checked else "")

    def add_row(key, label, checked):
        key_lower = key.lower()
        if key_lower in checkboxes_by_key:
            # A later add_row() call for the same key (e.g. the installed-
            # apps scan finding an exe that a "quick re-add" row already
            # added unchecked) should still be able to check it -- only
            # skip re-adding the row itself, not the checked state.
            if checked:
                existing_checkbox, _ = checkboxes_by_key[key_lower]
                existing_checkbox.setChecked(True)
            return
        checkbox = QCheckBox(label)
        checkbox.setChecked(checked)
        _apply_checked_style(checkbox, checked)
        checkbox.toggled.connect(lambda is_checked, cb=checkbox: _apply_checked_style(cb, is_checked))
        layout.insertWidget(layout.count() - 1, checkbox)
        checkboxes_by_key[key_lower] = (checkbox, key)

    def add_separator_label(text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: #555;")
        layout.insertWidget(layout.count() - 1, label)

    for item in items:
        key = key_fn(item)
        add_row(key, label_fn(item), checked=key.lower() in checked_keys)

    # exposed as attributes rather than extra return values, so existing
    # 3-tuple unpacking call sites (container, checkboxes, add_row) keep
    # working unchanged even though this extra helper exists
    add_row.add_separator_label = add_separator_label

    return scroll, checkboxes_by_key, add_row


def get_checked(checkboxes_by_key):
    """Returns the original (non-lowercased) keys whose checkbox is checked."""
    return [key for checkbox, key in checkboxes_by_key.values() if checkbox.isChecked()]
