"""What the tour says. Short chapters, plain words, one idea per step. Each chapter can be skipped on its
own, and Options > Help lists them so one can be played again."""
import os
import webbrowser

from PySide6.QtWidgets import QApplication, QWidget

import tasks_store
from qt_ui.tour import Chapter, Step

EXTENSION_REPO = "https://github.com/ricewas-mis-taken/CARMEN-chrome-extension"


def find(window, page_key, tour_id):
    """A widget on a page tagged with a tour id (Home rebuilds its cards, so this looks again each time)."""
    page = window._pages.get(page_key)
    if page is None:
        return None
    for child in page.findChildren(QWidget):
        if child.property("tourId") == tour_id and child.isVisible():
            return child
    return None


def extension_folder():
    """The chrome/ folder of the browser extension next to this app, if it is there."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(os.path.dirname(here), "carmen-extension", "chrome"),
                      os.path.join(here, "chrome")):
        if os.path.isfile(os.path.join(candidate, "manifest.json")):
            return candidate
    return None


def _copy(text):
    QApplication.clipboard().setText(text)


def _task_editor_open():
    return any(w.isVisible() and w.windowTitle().endswith("New Task") for w in QApplication.topLevelWidgets())


def chapters(window):
    nav = lambda key: (lambda: window._nav_buttons.get(key))      # noqa: E731
    page = lambda key: (lambda: window._pages.get(key))           # noqa: E731
    tag = lambda key, tid: (lambda: find(window, key, tid))       # noqa: E731
    first_count = len(tasks_store.load_tasks())
    folder = extension_folder()

    extension_buttons = []
    if folder:
        extension_buttons = [("Copy the folder path", lambda: _copy(folder)),
                             ("Open the folder", lambda: os.startfile(folder))]
    else:
        extension_buttons = [("Open the extension page", lambda: webbrowser.open(EXTENSION_REPO))]

    return [
        Chapter("welcome", "Welcome", "What Carmen is", [
            Step("Welcome to Carmen",
                 "Carmen helps you do the things you say you will do. You set goals, it keeps distractions out "
                 "while you work, it brings old problems back at the right time, and it keeps a score so you can "
                 "see how you are really doing.<br><br>This tour takes a few minutes. Every part can be skipped, "
                 "and you can play it again from Options, then Help."),
            Step("You are in charge of the pace",
                 "Use <b>Next</b> to go on, <b>Back</b> to go back, <b>Skip this part</b> to jump to the next "
                 "section, or <b>Skip the tour</b> to leave. The arrow keys and Esc work too."),
        ]),
        Chapter("around", "Getting around", "The pages", [
            Step("The sidebar", "Everything lives here. Click a name to open that page.",
                 target=lambda: window._sidebar, page="home"),
            Step("Home", "Where today starts: what is running, today's goals, your score and your streak.",
                 target=nav("home"), page="home"),
            Step("Focus", "Start a session here. While one runs, Carmen can block the apps and sites that pull you away.",
                 target=nav("focus"), page="focus"),
            Step("Tasks", "The things you work on every day or week, each with a goal. Sessions you start from a task count toward it.",
                 target=nav("tasks"), page="tasks"),
            Step("The Board", "A ranked list of everything you need to do, with a quick notes box underneath.",
                 target=nav("board"), page="board"),
            Step("Review", "Problems you want to remember. Carmen brings each one back right before you would forget it.",
                 target=nav("review"), page="review"),
            Step("Scribe", "An endless board to draw and think on. Pen, shapes, text and images.",
                 target=nav("scribe"), page="scribe"),
            Step("Screen Time and History", "Screen Time shows where your hours went. Session History lists every session you have run.",
                 target=nav("screentime"), page="screentime"),
        ]),
        Chapter("task", "Your first task", "Make a task", [
            Step("Make your first task",
                 "A task is something you want to work on regularly, like reading or practice. It has a goal for "
                 "each day or week. Click <b>Add recurring task</b> to make one.<br><br>"
                 "Not now? Use <b>Skip this part</b>.",
                 target=lambda: getattr(window._pages.get("tasks"), "_add_button", None), page="tasks",
                 wait_for=_task_editor_open),
            Step("Fill it in",
                 "Give it a name and a colour, choose how often it repeats, and set the time you want to give it. "
                 "Rules for apps and sites are optional, and you can change everything later.<br><br>"
                 "Press <b>Save</b> when you are done. I will wait.",
                 free=True, wait_for=lambda: len(tasks_store.load_tasks()) > first_count),
            Step("That is a task",
                 "It now shows up on the Tasks page with a ring that fills as you work, and on Home under today's goals. "
                 "Start a session from its card and the time counts toward its goal.",
                 target=page("tasks"), page="tasks"),
        ]),
        Chapter("focus", "Focus sessions", "Working with a lock", [
            Step("Start a session",
                 "Type how many minutes, or use a Pomodoro (work, then rest, again) or Burnout (no time limit, you stop when you are done).",
                 target=page("focus"), page="focus"),
            Step("The lock",
                 "A session can be soft (a gentle reminder when you drift) or hard (blocked apps and sites are shut). "
                 "Pick the apps and sites to block on the task or when you start."),
            Step("Pausing and unblocking",
                 "Pausing asks why: a break, an interruption, being stuck, or checking something. Checking something "
                 "comes out of a small budget, so a pause cannot quietly turn into an escape. Unblocking a site asks "
                 "what you will do with it and for how long, and past three a day you wait two minutes."),
        ]),
        Chapter("review", "Reviews", "Remembering things", [
            Step("Add what you want to remember",
                 "Add a problem, or paste a list. After you try it, tell Carmen how it went and it picks the next "
                 "day to see it again. Hard ones come back sooner.",
                 target=page("review"), page="review"),
            Step("The week ahead",
                 "This shows what you have to review on each day and which days are heavy. Drag a review to a lighter "
                 "day. Earlier is free, each day later costs three points.",
                 target=lambda: getattr(window._pages.get("review"), "_week_button", None), page="review"),
            Step("Set aside, at no cost",
                 "Right-click a problem and choose <b>Set aside</b> when you do not need it for a while. It is never "
                 "due and never costs you points, and you can put it back any time."),
        ]),
        Chapter("score", "Your score", "How you are doing", [
            Step("The Carmen Score",
                 "One number from 0 to 1000. It starts at 500 and moves a little every day: up when you meet "
                 "your goals and review on time, down when you do not. It never resets, so it shows the long run.",
                 target=tag("home", "score"), page="home"),
            Step("The weekly report",
                 "Once a week Carmen shows how the score moved, where points came from and went, and one thing to "
                 "change. Last week is history, not debt. You can open it from the score card."),
        ]),
        Chapter("extension", "Browser extension", "Block sites in your browser", [
            Step("Why the extension",
                 "The desktop app can block other apps. The browser extension blocks websites, and it shows your "
                 "timer in the toolbar. It talks to this app on your own computer, nothing is sent anywhere."),
            Step("Install it in Chrome or Edge",
                 "1. Open <b>chrome://extensions</b> (or edge://extensions).<br>"
                 "2. Turn on <b>Developer mode</b>.<br>"
                 "3. Click <b>Load unpacked</b> and choose the extension's <b>chrome</b> folder.<br>"
                 "Firefox: open about:debugging, choose <b>This Firefox</b>, then <b>Load Temporary Add-on</b> and pick "
                 "<b>firefox/manifest.json</b>.",
                 buttons=extension_buttons, free=True),
        ]),
        Chapter("more", "Options and help", "Make it yours", [
            Step("Options",
                 "Colours, the Discord status that shows what you are doing, and Help live in Options. "
                 "Help is where you can play this tour again.",
                 target=lambda: getattr(window._account_area, "_options_button", None)),
            Step("You are all set",
                 "Start with a task, then a session. The score will do the rest of the keeping track.<br><br>"
                 "You can find this tour again in Options, then Help."),
        ]),
    ]
