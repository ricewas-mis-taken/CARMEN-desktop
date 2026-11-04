"""Is someone actually at the computer? Screen time only counts time with a person there.

A window being open and in front proves nothing: it can sit on screen while you are away. So time counts only
while there was a key press or mouse move in the last IDLE_AFTER_SECONDS, with two exceptions where watching
is normal and needs no input: a browser tab that is playing sound (the extension reports it) and a few video
or call apps in front."""
import time

import auto_updater

IDLE_AFTER_SECONDS = 120
MEDIA_REPORT_SECONDS = 90          # a "sound is playing" report from the browser stays good this long
MEDIA_PROCESSES = {"vlc.exe", "mpv.exe", "zoom.exe", "teams.exe", "ms-teams.exe", "wmplayer.exe",
                   "movies & tv", "video.ui.exe"}

_media_until = 0.0


def idle_seconds():
    return auto_updater.user_idle_seconds()


def note_media(playing):
    """The browser says a tab is (or is no longer) playing sound."""
    global _media_until
    _media_until = time.time() + MEDIA_REPORT_SECONDS if playing else 0.0


def media_playing():
    return time.time() < _media_until


def is_active(process_name=None):
    idle = idle_seconds()
    if idle is None or idle < IDLE_AFTER_SECONDS:
        return True                      # cannot tell (not Windows) counts as present, as before
    if media_playing():
        return True
    return (process_name or "").lower() in MEDIA_PROCESSES
