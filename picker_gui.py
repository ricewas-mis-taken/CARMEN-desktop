"""Entry points for the tray-launched blocklist picker and start-session
timer dialog. Widget construction lives in qt_ui/picker_dialogs.py; this
module just marshals onto the Qt main thread, same public surface as
before the Tkinter->PySide6 migration so calendar_gui.py's callers don't
need to change."""
import qt_gui_thread
import qt_ui.picker_dialogs as picker_dialogs


def open_blocklist_picker():
    qt_gui_thread.run_on_gui_thread(picker_dialogs.open_blocklist_picker)


def open_timer_dialog():
    qt_gui_thread.run_on_gui_thread(picker_dialogs.open_timer_dialog)
