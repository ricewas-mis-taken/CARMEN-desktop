"""Windows toast notifications via winsdk (WinRT bindings) — chosen over
win10toast because reminder snoozing needs interactive toast buttons with an
activation callback, which win10toast's threaded/plain-message API can't do.

Toasts fire from this same process while it's alive, so no COM
background-activation registration (the kind a packaged/shortcut-installed
app would set up) is needed for the Activated callback to reach us — only
set_app_id() must run once at startup so ToastNotificationManager has an
AppUserModelID to publish under at all.
"""
import sys

if sys.platform == "darwin":
    from mac_os import calendar_toast_mac as _mac

    set_app_id = _mac.set_app_id
    show_toast = _mac.show_toast
else:
    import ctypes
    import os

    from winsdk.windows.data.xml.dom import XmlDocument
    from winsdk.windows.ui.notifications import ToastNotification, ToastNotificationManager

    from calendar_log import logger

    APP_ID = "CarmenFocus.CalendarApp"

    _notifier = None


    def set_app_id():
        """Must be called once, early in the process (main.py), before any
        show_toast() call — Windows silently refuses to attribute/display toasts
        from a process with no explicit AppUserModelID."""
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            logger.exception("set_app_id failed")
        _ensure_start_menu_shortcut()


    def _ensure_start_menu_shortcut():
        """SetCurrentProcessExplicitAppUserModelID alone isn't enough for an
        unpackaged script run via `python main.py` -- Windows Notification
        Platform only reliably renders a toast for an AUMID that's backed by a
        Start Menu shortcut carrying the same System.AppUserModel.ID property.
        Without one, ToastNotifier.show() raises nothing and just silently
        drops the toast, which is why show_toast() looked like it was "working"
        (no exception, ever) while nothing ever appeared on screen.

        Idempotent: only writes the .lnk if it's missing or stale, so this is
        cheap to call on every startup."""
        try:
            import pythoncom
            from win32com.propsys import propsys, pscon
            from win32com.shell import shell

            start_menu = os.path.join(
                os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs"
            )
            shortcut_path = os.path.join(start_menu, "Carmen Focus.lnk")
            target = sys.executable
            main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
            arguments = f'"{main_py}"'

            if os.path.exists(shortcut_path):
                existing = pythoncom.CoCreateInstance(
                    shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
                )
                existing.QueryInterface(pythoncom.IID_IPersistFile).Load(shortcut_path)
                if existing.GetPath(0)[0] == target and existing.GetArguments() == arguments:
                    return  # already registered correctly, nothing to do

            os.makedirs(start_menu, exist_ok=True)
            shell_link = pythoncom.CoCreateInstance(
                shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
            )
            shell_link.SetPath(target)
            shell_link.SetArguments(arguments)
            shell_link.SetWorkingDirectory(os.path.dirname(os.path.abspath(__file__)))

            prop_store = shell_link.QueryInterface(propsys.IID_IPropertyStore)
            prop_store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
            prop_store.Commit()

            shell_link.QueryInterface(pythoncom.IID_IPersistFile).Save(shortcut_path, True)
        except Exception:
            logger.exception("_ensure_start_menu_shortcut failed")


    def _get_notifier():
        global _notifier
        if _notifier is None:
            _notifier = ToastNotificationManager.create_toast_notifier(APP_ID)
        return _notifier


    def show_toast(title, body, buttons=None, on_action=None):
        """buttons: optional list of (argument, label) pairs rendered as toast
        action buttons. on_action(argument), if given, is called when the user
        clicks a button or the toast body itself (argument is "" for a body
        click) — invoked on a WinRT callback thread, NOT the Qt GUI thread or
        calendar_store's lock-owning thread, so callers must route any Qt work
        through qt_gui_thread.run_on_gui_thread and treat store access as
        already thread-safe (it is — calendar_store guards its own lock).

        Every failure here is logged and swallowed rather than raised — this is
        called from the background scheduler loop, which must never die from a
        notification failure."""
        try:
            actions_xml = ""
            if buttons:
                actions_xml = "<actions>" + "".join(
                    f'<action content="{label}" arguments="{argument}" activationType="foreground"/>'
                    for argument, label in buttons
                ) + "</actions>"

            xml = (
                "<toast activationType=\"foreground\">"
                "<visual><binding template=\"ToastGeneric\">"
                f"<text>{_escape(title)}</text>"
                f"<text>{_escape(body)}</text>"
                "</binding></visual>"
                f"{actions_xml}"
                "</toast>"
            )

            doc = XmlDocument()
            doc.load_xml(xml)
            toast = ToastNotification(doc)

            if on_action is not None:
                def _handle_activated(sender, args):
                    try:
                        on_action(getattr(args, "arguments", "") or "")
                    except Exception:
                        logger.exception("toast on_action callback failed")

                toast.add_activated(_handle_activated)

            _get_notifier().show(toast)
        except Exception:
            logger.exception("show_toast failed: %s / %s", title, body)


    def _escape(text):
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
