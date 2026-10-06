"""Verifies Supabase-issued access tokens against Supabase's own JWKS
endpoint -- this server never issues or signs its own tokens, and never
uses the service_role key. A verified token yields the caller's user_id
(the 'sub' claim) plus the raw token itself, which sync.py forwards
as-is to Supabase's PostgREST API so Supabase's RLS policies (see
schema.sql) enforce data access as a second layer, independent of
anything checked here.
"""
import logging
import os
import time
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

logger = logging.getLogger("sync_server.auth")

SUPABASE_JWKS_URL = os.environ.get("SUPABASE_JWKS_URL")

if not SUPABASE_JWKS_URL:
    logger.warning(
        "SUPABASE_JWKS_URL is not set -- every request will be rejected with 401 "
        "until it's configured in private/.env."
    )

_jwk_client = None

# PyJWKClient re-downloads the whole JWKS whenever a token's `kid` isn't in
# its cache. Verification happens before authentication, so anyone could send
# garbage tokens with random kids and make this server hit the JWKS endpoint
# once per request. Allow a refetch at most this often; a genuinely rotated
# key is picked up on the next allowed refresh.
JWKS_REFETCH_MIN_INTERVAL_SECONDS = 30


class _ThrottledJWKClient(PyJWKClient):
    _last_forced_refresh = float("-inf")

    def get_signing_keys(self, refresh=False):
        if refresh:
            now = time.monotonic()
            if now - self._last_forced_refresh < JWKS_REFETCH_MIN_INTERVAL_SECONDS:
                refresh = False
            else:
                self._last_forced_refresh = now
        return super().get_signing_keys(refresh=refresh)


def _get_jwk_client():
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = _ThrottledJWKClient(SUPABASE_JWKS_URL)
    return _jwk_client


@dataclass
class AuthContext:
    user_id: str
    access_token: str


def require_auth(authorization: str = Header(default=None)) -> AuthContext:
    """FastAPI dependency. Raises 401 if the Authorization header is
    missing, malformed, or carries an invalid/expired token.

    A plain `def`, not `async def`: PyJWKClient does blocking network I/O, and
    FastAPI runs sync dependencies in a worker thread instead of on the event
    loop (where a slow JWKS fetch stalled every other request)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization[len("Bearer "):].strip()

    if not SUPABASE_JWKS_URL:
        raise HTTPException(status_code=401, detail="Server auth is not configured")

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject claim")
    return AuthContext(user_id=user_id, access_token=token)
