"""System tray icon setup and menu."""
import pystray
from PIL import Image, ImageDraw

import history_gui
import picker_gui
import session_manager


def _generate_icon_image():
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    padding = 6
    draw.ellipse(
        (padding, padding, size - padding, size - padding),
        fill=(45, 140, 255, 255),
    )
    return image


def _format_status_text():
    status = session_manager.get_status()
    if not status["isActive"]:
        return "Carmen Focus — no active session"
    minutes = status["secondsRemaining"] // 60
    seconds = status["secondsRemaining"] % 60
    return (
        f"Carmen Focus — {minutes}m {seconds}s remaining\n"
        f"Lock mode: {status['lockMode']}\n"
        f"Violations: {status['violationCount']}"
    )


def _format_end_summary(summary):
    log = summary.get("violationLog", [])
    if not log:
        return "Session ended. No violations — nice work."

    counts = {}
    for entry in log:
        # Process violations have a "process" key, domain violations (from
        # the browser extension) have a "url" key instead.
        label = entry.get("process") or entry.get("url") or "?"
        counts[label] = counts.get(label, 0) + 1
    breakdown = ", ".join(f"{name} x{count}" for name, count in counts.items())
    return f"Session ended. {summary['violationCount']} violation(s): {breakdown}"


def build_tray_icon(on_quit):
    icon_image = _generate_icon_image()

    def on_status(icon, item):
        icon.notify(_format_status_text(), title="Carmen Focus Status")

    def on_pick_apps(icon, item):
        picker_gui.open_whitelist_picker()

    def on_start_session(icon, item):
        picker_gui.open_timer_dialog()

    def on_end_session(icon, item):
        summary = session_manager.end_session()
        icon.notify(_format_end_summary(summary), title="Carmen Focus")

    def on_view_history(icon, item):
        history_gui.open_history_viewer()

    def on_quit_clicked(icon, item):
        on_quit()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Status", on_status),
        pystray.MenuItem("Start Focus Session", on_start_session),
        pystray.MenuItem("Pick Apps to Whitelist", on_pick_apps),
        pystray.MenuItem("Session History", on_view_history),
        pystray.MenuItem("End Session (Nuclear)", on_end_session),
        pystray.MenuItem("Quit", on_quit_clicked),
    )

    icon = pystray.Icon("carmen_focus", icon_image, "Carmen Focus", menu)
    return icon


def run_tray(on_quit):
    """Runs the tray icon loop. Blocks — must be called from the main thread."""
    icon = build_tray_icon(on_quit)
    icon.run()
