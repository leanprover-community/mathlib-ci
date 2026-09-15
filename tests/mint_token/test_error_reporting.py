"""Each request function ends in `fail()` when the retries run out.

The tests call the real request functions with `urllib.request.urlopen` replaced by a
function that raises, so they cover the error handling around `urlopen_retrying`.
`test_retry.py` covers which failures reach the callers, and as what type.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable

import pytest

import mint_token

REQUESTS = [
    pytest.param(
        lambda: mint_token.get_actions_oidc_token(mint_token.OIDC_AUDIENCE),
        "Failed to fetch GitHub OIDC token",
        id="oidc",
    ),
    pytest.param(
        lambda: mint_token.exchange_oidc_for_keyvault_token("tenant", "client", "oidc-token"),
        "Failed to exchange OIDC token for Key Vault token",
        id="entra",
    ),
    pytest.param(
        lambda: mint_token.run_keyvault_sign("vault", "key", "", "ZGlnZXN0", "kv-token"),
        "Azure Key Vault signing failed",
        id="keyvault-sign",
    ),
    pytest.param(
        lambda: mint_token.github_request(mint_token.GITHUB_API_URL, "GET", "/meta", "app-jwt"),
        "GitHub API GET https://api.github.com/meta failed",
        id="github",
    ),
]


@pytest.fixture
def failing_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the only attempt of every request time out."""

    def explode(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    # An empty backoff means one attempt and no sleep.
    monkeypatch.setattr(mint_token, "RETRY_BACKOFF_SECONDS", ())
    monkeypatch.setattr(urllib.request, "urlopen", explode)


@pytest.mark.usefixtures("failing_socket")
@pytest.mark.parametrize(("request_fn", "message"), REQUESTS)
def test_request_fails_cleanly(
    request_fn: Callable[[], object],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Only the OIDC fetch reads these.
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://runner.test/idtoken?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")

    with pytest.raises(SystemExit) as excinfo:
        request_fn()

    assert excinfo.value.code == 1
    assert message in capsys.readouterr().err


@pytest.mark.usefixtures("failing_socket")
def test_github_network_failure_is_not_mistaken_for_a_404(capsys: pytest.CaptureFixture[str]) -> None:
    """A network failure on the org lookup ends the run.

    `resolve_installation_id` falls through to the user lookup only on a 404
    `GithubHttpError`.
    """
    with pytest.raises(SystemExit) as excinfo:
        mint_token.resolve_installation_id(mint_token.GITHUB_API_URL, "app-jwt", "leanprover-community")

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "/orgs/leanprover-community/installation failed" in stderr
    assert "/users/" not in stderr
