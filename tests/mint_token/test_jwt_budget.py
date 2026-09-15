"""The requests sent after `iat` is stamped fit inside the usable lifetime of the JWT.

One test checks the arithmetic on the constants. The other checks that `build_app_jwt`
gets the Key Vault credentials before it stamps `iat`.
"""

from __future__ import annotations

import json

import pytest

import mint_token
from mint_token import (
    CLOCK_SKEW_BACKDATE_SECONDS,
    JWT_EXPIRATION_SECONDS,
    MAX_JWT_CLOCK_REQUESTS,
    WORST_CASE_REQUEST_SECONDS,
    b64url_decode,
    b64url_encode,
)


def test_retry_budget_fits_inside_the_jwt_lifetime() -> None:
    usable_lifetime = JWT_EXPIRATION_SECONDS - CLOCK_SKEW_BACKDATE_SECONDS

    assert MAX_JWT_CLOCK_REQUESTS * WORST_CASE_REQUEST_SECONDS <= usable_lifetime


def test_credentials_are_fetched_before_the_jwt_clock_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_app_jwt` fetches the OIDC and Key Vault tokens before it stamps `iat`."""
    events: list[str] = []
    entra_finished_at: list[int] = []

    def fake_oidc(audience: str) -> str:
        events.append("oidc")
        return "oidc-token"

    def fake_exchange(tenant_id: str, client_id: str, oidc_token: str) -> str:
        events.append("entra")
        entra_finished_at.append(int(mint_token.time.time()))
        return "kv-token"

    def fake_sign(*args: str) -> str:
        events.append("sign")
        return b64url_encode(b"signature-bytes")

    monkeypatch.setattr(mint_token, "get_actions_oidc_token", fake_oidc)
    monkeypatch.setattr(mint_token, "exchange_oidc_for_keyvault_token", fake_exchange)
    monkeypatch.setattr(mint_token, "run_keyvault_sign", fake_sign)

    jwt = mint_token.build_app_jwt(
        "123456", "vault", "key", "", "client-id", "tenant-id", JWT_EXPIRATION_SECONDS
    )

    assert events == ["oidc", "entra", "sign"]
    payload = json.loads(b64url_decode(jwt.split(".")[1]))
    # `iat` is backdated, so undo that before comparing against the wall clock.
    assert payload["iat"] + CLOCK_SKEW_BACKDATE_SECONDS >= entra_finished_at[0]
    assert payload["exp"] - payload["iat"] == JWT_EXPIRATION_SECONDS
    assert payload["iss"] == "123456"
