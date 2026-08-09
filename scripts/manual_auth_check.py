"""Manual, interactive smoke test for auth_manager.py -- signup -> login ->
get_current_user -> logout, run from the command line against your real
Supabase project (via private/.env). Not part of the app and not picked up
by pytest.

Use a throwaway test email, not a real account -- this script does not
clean up the created account afterward.

Run from the repo root:
    python scripts/manual_auth_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_manager


def main():
    print(f"Supabase configured: {bool(auth_manager.SUPABASE_URL and auth_manager.SUPABASE_PUBLISHABLE_KEY)}")
    if not (auth_manager.SUPABASE_URL and auth_manager.SUPABASE_PUBLISHABLE_KEY):
        print(f"Missing SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY in {auth_manager.ENV_PATH} -- aborting.")
        return

    email = input("Throwaway test email: ").strip()
    password = input("Password (min 6 chars): ").strip()

    print("\n--- signup() ---")
    success, error = auth_manager.signup(email, password)
    print(f"success={success} error={error}")
    if not success:
        return

    print("\n--- login() ---")
    success, error = auth_manager.login(email, password)
    print(f"success={success} error={error}")
    if not success:
        print(
            "If this failed with 'confirm your email', check the Supabase project's "
            "auth settings -- email confirmation may be required before login works."
        )
        return

    print("\n--- get_current_user() ---")
    print(auth_manager.get_current_user())

    print("\n--- is_logged_in() ---")
    print(auth_manager.is_logged_in())

    print("\n--- get_access_token() (truncated) ---")
    token = auth_manager.get_access_token()
    print((token[:20] + "...") if token else None)

    print("\n--- logout() ---")
    auth_manager.logout()
    print(f"is_logged_in() after logout: {auth_manager.is_logged_in()}")
    print(f"get_current_user() after logout: {auth_manager.get_current_user()}")


if __name__ == "__main__":
    main()
