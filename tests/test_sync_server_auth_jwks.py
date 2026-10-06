"""require_auth runs before authentication, so unauthenticated garbage tokens
must not each trigger a blocking JWKS download on the event loop."""
import base64
import inspect
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt import PyJWKClient

from sync_server import auth


def _b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _token(kid):
    return f"{_b64({'alg': 'ES256', 'kid': kid, 'typ': 'JWT'})}.{_b64({'sub': 'x'})}.AAAA"


def _make_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm
    jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(ec.generate_private_key(ec.SECP256R1()).public_key()))
    jwk.update({"kid": "real-key", "use": "sig", "alg": "ES256"})
    return jwk


_REAL_KEY = _make_key()


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_fetch(self):
        calls.append(1)
        data = {"keys": [_REAL_KEY]}
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)  # what the real fetch_data does
        return data

    monkeypatch.setattr(PyJWKClient, "fetch_data", fake_fetch)
    monkeypatch.setattr(auth, "SUPABASE_JWKS_URL", "http://127.0.0.1:9/jwks")
    monkeypatch.setattr(auth, "_jwk_client", None)
    app = FastAPI()

    @app.get("/x")
    def route(ctx: auth.AuthContext = Depends(auth.require_auth)):
        return {"ok": True}

    c = TestClient(app)
    c.jwks_calls = calls
    return c


def test_garbage_tokens_with_random_kids_do_not_each_refetch_jwks(client):
    for i in range(20):
        resp = client.get("/x", headers={"Authorization": f"Bearer {_token(f'kid{i}')}"})
        assert resp.status_code == 401
    assert len(client.jwks_calls) <= 2, len(client.jwks_calls)


def test_require_auth_does_not_run_blocking_io_on_the_event_loop():
    assert not inspect.iscoroutinefunction(auth.require_auth)


def test_missing_header_still_401(client):
    assert client.get("/x").status_code == 401
