"""CARMEN sync_server -- a FastAPI backend for multi-device sync.

Loads the same private/.env used by the main desktop app (one directory
up from this package) so SUPABASE_URL/SUPABASE_JWKS_URL/etc. don't need
to be duplicated into a second secrets file.
"""
import os

from dotenv import load_dotenv

_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "private", ".env"
)
load_dotenv(_ENV_PATH)
