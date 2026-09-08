from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth import (
    GOOGLE_AUTH_SCOPES,
    GoogleAuthState,
    build_google_auth_authorization_url,
    decode_google_auth_state,
    encode_google_auth_state,
    sanitize_frontend_redirect_path,
)
from app.config import settings


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "http",
            "method": "GET",
            "path": "/auth/google/authorize",
            "headers": [],
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 51234),
        }
    )


def test_google_auth_state_is_signed_and_carries_redirect() -> None:
    state = GoogleAuthState(
        nonce="nonce",
        issued_at=int(datetime.now(timezone.utc).timestamp()),
        redirect_path="/notes?filter=recent",
    )

    token = encode_google_auth_state(state)
    decoded = decode_google_auth_state(token)

    assert decoded.nonce == "nonce"
    assert decoded.redirect_path == "/notes?filter=recent"
    with pytest.raises(HTTPException):
        decode_google_auth_state(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_google_authorization_url_uses_login_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "AUTH_OAUTH_REDIRECT_BASE_URL", None)
    monkeypatch.setattr(settings, "GOOGLE_AUTH_REDIRECT_PATH", "/auth/google/callback")

    url = build_google_auth_authorization_url(_request(), "/dashboard?from=login")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    assert query["scope"] == [" ".join(GOOGLE_AUTH_SCOPES)]
    assert query["response_type"] == ["code"]
    assert decode_google_auth_state(query["state"][0]).redirect_path == "/dashboard?from=login"


def test_frontend_redirect_path_is_app_local() -> None:
    assert sanitize_frontend_redirect_path("/dashboard") == "/dashboard"
    assert sanitize_frontend_redirect_path("/notes?tab=mine") == "/notes?tab=mine"
    assert sanitize_frontend_redirect_path("https://example.com") == "/dashboard"
    assert sanitize_frontend_redirect_path("//example.com") == "/dashboard"
    assert sanitize_frontend_redirect_path("\\admin") == "/dashboard"
