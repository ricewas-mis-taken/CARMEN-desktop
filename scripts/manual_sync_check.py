"""Manual, one-shot smoke test for sync_client.sync_now() -- run from the
command line against your REAL local data and REAL logged-in account, not
a throwaway. Not part of the app and not picked up by pytest.

BACK UP YOUR REAL DATA FIRST, same as Phase 2's migration testing:
    private\\tasks.json
    private\\board.json
    private\\calendar.db (and its -shm/-wal siblings, if present)

This calls sync_now() exactly once and prints the result -- it does not
loop, and it does not touch anything until sync_server confirms it's
reachable. Make sure sync_server is already running (see
sync_server/README.md) before running this.

Run from the repo root:
    python scripts/manual_sync_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_manager
import sync_client


def main():
    print(f"SYNC_SERVER_URL: {sync_client.SYNC_SERVER_URL}")

    if not auth_manager.is_logged_in():
        print("Not logged in yet -- log in with your real account (not a throwaway).")
        email = input("Email: ").strip()
        password = input("Password: ").strip()
        success, error = auth_manager.login(email, password)
        if not success:
            print(f"Login failed: {error}")
            return
        print("Logged in.")

    user = auth_manager.get_current_user()
    print(f"Signed in as: {user['email'] if user else '(unknown)'}")

    confirm = input(
        "\nThis will push your local tasks/board/calendar changes to sync_server and pull\n"
        "back anything already there. Have you backed up private/tasks.json, "
        "private/board.json,\nand private/calendar.db? [y/N] "
    ).strip().lower()
    if confirm != "y":
        print("Aborting -- back up your data first.")
        return

    print("\n--- sync_now() ---")
    result = sync_client.sync_now()
    print(f"success={result.success}")
    print(f"pushed={result.pushed} pulled={result.pulled} skipped={result.skipped} failed={result.failed}")
    if result.failed:
        print("failed > 0 -- check the terminal above for _log_record_failure's per-record traceback(s).")
    if result.error:
        print(f"error={result.error}")
    print(f"not_logged_in={result.not_logged_in}")


if __name__ == "__main__":
    main()
