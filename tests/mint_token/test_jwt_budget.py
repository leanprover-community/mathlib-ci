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


class FakeClock:
    """Stand-in for the `time` module. `time()` returns a value the test advances."""

    def __init__(self, now: int) -> None:
        self.now = now

    def time(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


def test_retry_budget_fits_inside_the_jwt_lifetime() -> None:
    usable_lifetime = JWT_EXPIRATION_SECONDS - CLOCK_SKEW_BACKDATE_SECONDS

    assert MAX_JWT_CLOCK_REQUESTS * WORST_CASE_REQUEST_SECONDS <= usable_lifetime


def test_credentials_are_fetched_before_the_jwt_clock_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_app_jwt` fetches the OIDC and Key Vault tokens before it stamps `iat`."""
    clock = FakeClock(1_000_000)

    def fake_oidc(audience: str) -> str:
        clock.advance(WORST_CASE_REQUEST_SECONDS)
        return "oidc-token"

    def fake_exchange(tenant_id: str, client_id: str, oidc_token: str) -> str:
        clock.advance(WORST_CASE_REQUEST_SECONDS)
        return "kv-token"

    def fake_sign(*args: str) -> str:
        return b64url_encode(b"signature-bytes")

    monkeypatch.setattr(mint_token, "time", clock)
    monkeypatch.setattr(mint_token, "get_actions_oidc_token", fake_oidc)
    monkeypatch.setattr(mint_token, "exchange_oidc_for_keyvault_token", fake_exchange)
    monkeypatch.setattr(mint_token, "run_keyvault_sign", fake_sign)

    jwt = mint_token.build_app_jwt("123456", "vault", "key", "", "client-id", "tenant-id", JWT_EXPIRATION_SECONDS)

    payload = json.loads(b64url_decode(jwt.split(".")[1]))
    assert payload["iat"] == clock.now - CLOCK_SKEW_BACKDATE_SECONDS
