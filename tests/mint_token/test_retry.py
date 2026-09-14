"""Tests for mint_token.urlopen_retrying: which failures are retried, and how.

The tests drive the policy through its `send` and `sleep` parameters with recording fakes.
"""

from __future__ import annotations

import http.client
import io
import urllib.error
import urllib.request

import pytest

from mint_token import RETRY_BACKOFF_SECONDS, urlopen_retrying

_REQ = urllib.request.Request("https://example.test/hop", method="POST", data=b"{}")


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(_REQ.full_url, code, "msg", {}, io.BytesIO(body))


class Transport:
    """Fake `send` that returns or raises each scripted outcome in turn and counts the calls."""

    def __init__(self, *outcomes: bytes | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, req: urllib.request.Request) -> bytes:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "failure",
    [http_error(500), http.client.IncompleteRead(b"hel", 95), TimeoutError("timed out")],
    ids=["5xx", "http-exception", "os-error"],
)
def test_transient_failure_is_retried_after_the_first_backoff(failure: BaseException) -> None:
    transport = Transport(failure, b"ok")
    sleeps: list[float] = []

    assert urlopen_retrying(_REQ, send=transport, sleep=sleeps.append) == b"ok"
    assert transport.calls == 2
    assert sleeps == [RETRY_BACKOFF_SECONDS[0]]


def test_gives_up_after_the_last_backoff(capsys: pytest.CaptureFixture[str]) -> None:
    failures = [http_error(503, b"busy") for _ in range(len(RETRY_BACKOFF_SECONDS) + 1)]
    transport = Transport(*failures)
    sleeps: list[float] = []

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urlopen_retrying(_REQ, send=transport, sleep=sleeps.append)

    assert excinfo.value is failures[-1]
    assert transport.calls == len(failures)
    assert sleeps == list(RETRY_BACKOFF_SECONDS)
    # Retried responses are closed; the last is left open for the caller's `err.read()`.
    assert [f.fp.closed for f in failures] == [True] * len(RETRY_BACKOFF_SECONDS) + [False]
    assert excinfo.value.read() == b"busy"
    assert capsys.readouterr().err.count("retrying in") == len(RETRY_BACKOFF_SECONDS)


def test_4xx_raises_immediately_with_body_unread() -> None:
    err = http_error(401, b"bad credentials")
    transport = Transport(err, b"never sent")
    sleeps: list[float] = []

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urlopen_retrying(_REQ, send=transport, sleep=sleeps.append)

    assert excinfo.value is err
    assert transport.calls == 1
    assert sleeps == []
    assert err.read() == b"bad credentials"


def test_unrelated_errors_propagate() -> None:
    """An exception outside the transient set propagates on the first attempt."""
    transport = Transport(ValueError("boom"), b"never sent")

    with pytest.raises(ValueError):
        urlopen_retrying(_REQ, send=transport, sleep=lambda _: None)

    assert transport.calls == 1
