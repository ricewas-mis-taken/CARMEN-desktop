"""Logs in with an existing Supabase account and prints the raw access
token, for pasting into manual curl tests against sync_server. Not part
of the app, not pytest-collected.

Run from the repo root:
    python scripts/print_access_token.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_manager


def main():
    email = input("Email: ").strip()
    password = input("Password: ").strip()

    success, error = auth_manager.login(email, password)
    if not success:
        print(f"Login failed: {error}")
        return

    print("\nAccess token:")
    print(auth_manager.get_access_token())


if __name__ == "__main__":
    main()
