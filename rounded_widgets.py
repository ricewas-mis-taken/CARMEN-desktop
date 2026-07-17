"""Canvas-based rounded-rect helpers for a Google Calendar / macOS Calendar
style look — plain Tkinter's native Frame/Button/Label have no rounded-corner
support at all, and this app is plain Tkinter throughout (see gui_thread.py's
single shared Tk() root; every popup is a Toplevel on it). Rather than pull
in a second GUI framework (CustomTkinter/ttkbootstrap) partway through the
app's life — real migration risk against that shared-root architecture, and
overkill for what's really just "rounded rectangles and a hover state" — this
draws the same effect with plain Canvas primitives.

Purely a visual layer: nothing here touches app state, only presentation.
"""
import tkinter as tk


def rounded_rect_points(x1, y1, x2, y2, radius):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]


def draw_rounded_rect(canvas, x1, y1, x2, y2, radius=8, **kwargs):
    return canvas.create_polygon(rounded_rect_points(x1, y1, x2, y2, radius), smooth=True, **kwargs)


def shade(hex_color, percent):
    """Lightens (percent > 0) or darkens (percent < 0) a hex color — used
    for hover states so a button's fill shifts without needing a second
    hand-picked color for every variant."""
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return hex_color

    def adjust(c):
        if percent >= 0:
            return min(255, int(c + (255 - c) * percent / 100))
        return max(0, int(c + c * percent / 100))

    r, g, b = adjust(r), adjust(g), adjust(b)
    return f"#{r:02x}{g:02x}{b:02x}"


def _widget_bg(widget):
    try:
        return widget.cget("bg")
    except Exception:
        return "#ffffff"


class RoundedButton(tk.Canvas):
    """A flat, rounded-rect button drawn on a Canvas: centered or
    left-anchored text, a hover-state fill shift, click bound to `command`.
    Sized to fit its text plus padding unless width/height are given
    explicitly (used for the sidebar's full-width nav items)."""

    def __init__(
        self, parent, text, command=None, bg="#2d8cff", hover_bg=None, fg="white",
        font=("Segoe UI", 9), radius=8, padx=14, pady=7, width=None, height=None,
        anchor="center", parent_bg=None, **kwargs,
    ):
        self._bg = bg
        self._hover_bg = hover_bg or shade(bg, -10 if _is_dark(bg) else -8)
        self._fg = fg
        self._command = command
        self._radius = radius

        if width is None or height is None:
            probe = tk.Label(parent, text=text, font=font)
            probe.update_idletasks()
            width = width or (probe.winfo_reqwidth() + padx * 2)
            height = height or (probe.winfo_reqheight() + pady * 2)
            probe.destroy()

        super().__init__(
            parent, width=width, height=height, highlightthickness=0,
            bg=parent_bg or _widget_bg(parent), bd=0, **kwargs,
        )

        self._rect = draw_rounded_rect(self, 1, 1, width - 1, height - 1, radius, fill=bg, outline="")
        text_x = padx if anchor == "w" else width / 2
        self._label = self.create_text(
            text_x, height / 2, text=text, fill=fg, font=font,
            anchor="w" if anchor == "w" else "center",
        )

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._active = False

    def _on_enter(self, _e=None):
        if not self._active:
            self.itemconfig(self._rect, fill=self._hover_bg)

    def _on_leave(self, _e=None):
        if not self._active:
            self.itemconfig(self._rect, fill=self._bg)

    def _on_click(self, _e=None):
        if self._command:
            self._command()

    def set_active(self, active, active_bg=None):
        """Pins the fill to a highlighted state (e.g. the selected sidebar
        tab) regardless of hover — active_bg defaults to the hover color."""
        self._active = active
        self.itemconfig(self._rect, fill=(active_bg or self._hover_bg) if active else self._bg)

    def set_text(self, text):
        self.itemconfig(self._label, text=text)


def _is_dark(hex_color):
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5
    except Exception:
        return False


def draw_pill(canvas, x1, y1, x2, y2, fill, text=None, text_fill="white", font=("Segoe UI", 8), radius=None):
    """Draws one rounded pill (used for month-grid event chips and the
    hourly view's event blocks) and, if given, centers truncated-safe text
    inside it. radius defaults to half the pill's height, i.e. a full
    stadium/pill shape for short chips."""
    if radius is None:
        radius = (y2 - y1) / 2
    draw_rounded_rect(canvas, x1, y1, x2, y2, radius, fill=fill, outline="")
    if text is not None:
        canvas.create_text(
            (x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=text_fill, font=font,
        )
