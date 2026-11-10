"""Python's cycle collector must only run on the Qt main thread.

A Qt widget caught in a reference cycle is destroyed by whichever thread happens to trigger a collection. When
that is a worker thread (sync, the tracker, the web server), Qt objects are freed off their own thread, which
corrupts the heap and shows up much later, in some unrelated place, as a crash (exit code 0xc0000374).
So the automatic collector is switched off and the main thread runs it itself, on a timer."""
import gc

ACTIVE = False
_timer = None


def install(parent=None):
    """Call once on the Qt main thread after QApplication exists."""
    global ACTIVE, _timer
    from PySide6.QtCore import QTimer
    gc.disable()
    ACTIVE = True
    ticks = {"n": 0}

    def collect():
        ticks["n"] += 1
        gc.collect(2 if ticks["n"] % 12 == 0 else 1)        # a young sweep every 5 s, everything every minute

    _timer = QTimer(parent)
    _timer.setInterval(5000)
    _timer.timeout.connect(collect)
    _timer.start()


def resume():
    """Turns the automatic collector back on, unless the main-thread collector is in charge."""
    if not ACTIVE:
        gc.enable()
