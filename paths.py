"""Where the app keeps its data.

Every store used to build its own <repo>/private/... path, which meant a test (or a
script) could only be kept away from the real files by patching each constant one by
one -- and a constant nobody remembered to patch silently read and wrote the owner's
real data. They now all come from here, and CARMEN_PRIVATE_DIR points the whole app at
a different folder (the test suite sets it before anything is imported).
"""
import os

REPO = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.environ.get("CARMEN_PRIVATE_DIR") or os.path.join(REPO, "private")


def private_path(*parts):
    """A path inside the data folder, e.g. private_path("data", "board_photos")."""
    return os.path.join(PRIVATE, *parts)
