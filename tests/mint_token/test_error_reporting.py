"""Every hop ends in a clean `fail()`, not a traceback, once the retries run out.

`urlopen_retrying` treats a wider set of exceptions as transient than the callers used
to catch, so a terminal dropped connection or read timeout escaped as an unhandled
exception. These tests drive the real callers through a failing socket layer so the
retry policy and the error handling around it cannot drift apart again.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest

import mint_token

TERMINAL_FAILURES = [
    TimeoutError("timed out"),
    http.client.IncompleteRead(b"hel", 95),
    ConnectionResetError(104, "Connection reset by peer"),
    urllib.error.URLError("name resolution failed"),
]
FAILURE_IDS = ["timeout", "incomplete-read", "connection-reset", "url-error"]

_parametrized = pytest.mark.parametrize("failure", TERMINAL_FAILURES, ids=FAILURE_IDS)


@pytest.fixture
def failing_socket(monkeypatch: pytest.MonkeyPatch) -> Callable[[BaseException], None]:
    """Return an installer that makes every send fail with the given exception."""

    def install(failure: BaseException) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise failure

        # An empty backoff leaves a single attempt, which is the terminal path under
        # test, and keeps these tests from sleeping through the real backoff.
        monkeypatch.setattr(mint_token, "RETRY_BACKOFF_SECONDS", ())
        monkeypatch.setattr(urllib.request, "urlopen", explode)

    return install


@_parametrized
def test_oidc_fetch_fails_cleanly(
    failure: BaseException,
    failing_socket: Callable[[BaseException], None],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://runner.test/idtoken?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")
    failing_socket(failure)

    with pytest.raises(SystemExit) as excinfo:
        mint_token.get_actions_oidc_token(mint_token.OIDC_AUDIENCE)

    assert excinfo.value.code == 1
    assert "Failed to fetch GitHub OIDC token" in capsys.readouterr().err


@_parametrized
def test_entra_exchange_fails_cleanly(
    failure: BaseException,
    failing_socket: Callable[[BaseException], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing_socket(failure)

    with pytest.raises(SystemExit) as excinfo:
        mint_token.exchange_oidc_for_keyvault_token("tenant", "client", "oidc-token")

    assert excinfo.value.code == 1
    assert "Failed to exchange OIDC token for Key Vault token" in capsys.readouterr().err


@_parametrized
def test_keyvault_sign_fails_cleanly(
    failure: BaseException,
    failing_socket: Callable[[BaseException], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing_socket(failure)

    with pytest.raises(SystemExit) as excinfo:
        mint_token.run_keyvault_sign("vault", "key", "", "ZGlnZXN0", "kv-token")

    assert excinfo.value.code == 1
    assert "Azure Key Vault signing failed" in capsys.readouterr().err


@_parametrized
def test_github_request_fails_cleanly(
    failure: BaseException,
    failing_socket: Callable[[BaseException], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing_socket(failure)

    with pytest.raises(SystemExit) as excinfo:
        mint_token.github_request(mint_token.GITHUB_API_URL, "GET", "/meta", "app-jwt")

    assert excinfo.value.code == 1
    assert "GitHub API GET https://api.github.com/meta failed" in capsys.readouterr().err


def test_github_network_failure_is_not_mistaken_for_a_404(
    failing_socket: Callable[[BaseException], None],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dropped org lookup must abort, not fall through to the user lookup.

    `resolve_installation_id` reads a 404 off `GithubHttpError` to decide that an owner
    is a user rather than an org, so a network failure must not arrive as that type.
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "leanprover-community/mathlib4")
    failing_socket(TimeoutError("timed out"))

    with pytest.raises(SystemExit) as excinfo:
        mint_token.resolve_installation_id(mint_token.GITHUB_API_URL, "app-jwt", "leanprover-community")

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "/orgs/leanprover-community/installation failed" in stderr
    assert "/users/" not in stderr
