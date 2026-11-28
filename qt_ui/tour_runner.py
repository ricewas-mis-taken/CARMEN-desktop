"""Starting the tour: the first time Carmen opens, and again whenever someone asks (Options > Help)."""
import config

_window = None
_tour = None


def register(window):
    """Tells the runner which window the tour dims. Called once by the main window."""
    global _window
    _window = window


_preferences = None


def _build(chapter_ids, first_run=False):
    from qt_ui import tour, tour_content
    chapters = tour_content.chapters(_window)
    if chapter_ids:
        chapters = [c for c in chapters if c.id in chapter_ids] or chapters
    return tour.Tour(_window, chapters, on_finished=lambda completed: _finished(completed, first_run))


def _finished(_completed, first_run=False):
    """Skipped or finished, the tour does not come back by itself. The first
    tour, finished or skipped, ends on every option so people set their
    preferences before they start."""
    config.update_config(lambda cfg: cfg.update(tourDone=True))
    if first_run:
        show_preferences()


def show_preferences():
    global _preferences
    from qt_ui.options_dialog import OptionsDialog
    _preferences = OptionsDialog(setup=True)
    return _preferences


def start(chapter_ids=None, first_run=False):
    """Plays the tour (all of it, or only the chapters named)."""
    global _tour
    if _window is None:
        return None
    if _tour is not None and _tour.active:
        _tour.finish(completed=False)
    if _window.isMinimized() or not _window.isVisible():
        _window.showNormal()
        _window.raise_()
    _tour = _build(chapter_ids, first_run)
    _tour.start()
    return _tour


def start_if_new():
    """The first run only: nothing has been shown before and the window is really on screen."""
    if _window is None or config.load_config().get("tourDone"):
        return None
    if not _window.isVisible() or _window.isMinimized():
        return None
    return start(first_run=True)


def chapter_list():
    """[(id, title, summary)] for the Help list."""
    if _window is None:
        return []
    from qt_ui import tour_content
    return [(c.id, c.title, c.summary) for c in tour_content.chapters(_window)]
