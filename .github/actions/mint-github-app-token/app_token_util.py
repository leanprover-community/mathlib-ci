"""Building blocks for the mint in `mint_app_token.py`: the retrying bearer-token
transport, the input and answer checks, and the GITHUB_OUTPUT block.

Every function here is pure except `fetch_once`, which makes one HTTP
request, and `body_excerpt`, which reads the body of an error answer.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

ATTEMPTS = 3
TIMEOUT_SECONDS = 30

# The 4xx statuses that are not a verdict on the request: the server
# timed out reading it (408) or throttled it (429). They retry like a
# transient 5xx.
RETRYABLE_4XX = frozenset({408, 429})

# The broker answers 500 for a fault only an operator can clear, so a 500
# is a verdict on the request. Every other 5xx, including the Cloudflare
# edge statuses from 520 up, is transient.
VERDICT_5XX = frozenset({500, 501})

# How much of an error answer's body reaches the job log.
BODY_EXCERPT_CHARS = 200

# A Cloudflare browser integrity check in front of the broker answers 403
# (error 1010) to Python's default `Python-urllib/x.y` agent. A named
# agent passes and identifies the caller in the broker's logs.
USER_AGENT = "mathlib-ci/mint-github-app-token"

# What one HTTP request can raise. `OSError` covers `urllib.error.URLError`
# and its subclass `HTTPError`; `http.client.HTTPException` covers
# `IncompleteRead` on a truncated body.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class MintError(Exception):
    """A mint failure, with a one-line operator-facing message."""


# (url, bearer, method, body) -> response body. `body` is the JSON text of
# a POST, or None. Raises one of TRANSPORT_ERRORS on failure; an
# `HTTPError` carries the status.
Fetch = Callable[[str, str, str, "str | None"], str]


def is_printable_word(value: object) -> bool:
    """Whether the value is one printable word: non-empty, no whitespace,
    no control characters.

    `::add-mask::` masks one line, and a `name=value` line in
    GITHUB_OUTPUT carries one value, so the token must be one word
    before it reaches either. The app name is one word for the same
    reason: it is echoed in the job log.
    """
    return isinstance(value, str) and value != "" and value.isprintable() and not any(c.isspace() for c in value)


def check_broker_url(url: str) -> None:
    """Reject a broker URL that is not one https URL with a host.

    The OIDC token travels as a bearer to this URL, so the scheme must be
    https. `urlsplit` drops tab and newline characters, so the word
    check runs first.
    """
    if not is_printable_word(url):
        raise MintError("broker-url is not one https URL")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != "https" or not parts.hostname or port == -1:
        raise MintError("broker-url is not one https URL")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    urllib would follow a redirect of the POST as a GET and send the bearer
    token to the new location, which may be another host or plain http.
    A redirect is answered as an `HTTPError` with its 3xx status instead,
    which the mint reports as a verdict.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch_once(url: str, bearer: str, method: str, body: str | None) -> str:
    """One HTTP request with a bearer token; return the response body."""
    headers = {"Authorization": f"Bearer {bearer}", "User-Agent": USER_AGENT}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = body.encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def is_retryable_status(code: int) -> bool:
    """Whether an HTTP error status is transient: a 5xx other than a
    verdict, 408, or 429."""
    return (code >= 500 and code not in VERDICT_5XX) or code in RETRYABLE_4XX


def with_retries(call: Callable[[], str], sleep: Callable[[float], None] | None = None) -> str:
    """Call up to ATTEMPTS times and return the first answer.

    Transport faults and transient statuses retry with backoff. The
    OIDC request is idempotent. A retried broker request mints a second
    token after a lost answer; the first token expires unused, so the
    retry is safe. Any other status is a verdict on the request, so it
    raises at once. Any other exception is a bug and propagates
    untouched.
    """
    sleep = time.sleep if sleep is None else sleep
    for attempt in range(ATTEMPTS):
        last = attempt + 1 == ATTEMPTS
        try:
            return call()
        except urllib.error.HTTPError as error:
            if not is_retryable_status(error.code) or last:
                raise
        except TRANSPORT_ERRORS:
            if last:
                raise
        sleep(2**attempt)
    raise AssertionError("unreachable: the loop returns or raises")


def fetch_text(url: str, bearer: str, method: str, body: str | None) -> str:
    """The default transport: `fetch_once` under `with_retries`."""
    return with_retries(lambda: fetch_once(url, bearer, method, body))


def body_excerpt(error: urllib.error.HTTPError) -> str:
    """
    A fragment of the body of an error answer.
    """
    try:
        body = error.read().decode("utf-8", errors="replace")
    except TRANSPORT_ERRORS:
        return ""
    excerpt = " ".join("".join(c if c.isprintable() else " " for c in body).split())
    if len(excerpt) > BODY_EXCERPT_CHARS:
        excerpt = excerpt[:BODY_EXCERPT_CHARS] + "..."
    return excerpt


def oidc_token_url(request_url: str, audience: str) -> str:
    """The OIDC token endpoint with the audience selector appended, URL-encoded."""
    separator = "&" if "?" in request_url else "?"
    return f"{request_url}{separator}{urlencode({'audience': audience})}"


def parse_oidc_token(body: str) -> str:
    """The `value` of the GitHub OIDC token endpoint's answer."""
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    token = answer.get("value") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        raise MintError("the OIDC token response carried no value")
    return token


def parse_token(body: str) -> dict[str, str]:
    """Validate the broker's answer field by field.

    A 200 with a malformed body takes the same failure path as a
    transport error. The token is one printable word before it reaches
    the caller.
    """
    try:
        answer = json.loads(body)
    except ValueError:
        raise MintError("the broker answer was not JSON") from None
    if not isinstance(answer, dict):
        raise MintError("the broker answer was not a JSON object")
    token = answer.get("token")
    if not is_printable_word(token):
        raise MintError("the broker answer carried no well-formed token")
    # The grant name and the installation id are display-only. A missing
    # or malformed one prints as `?` and the mint succeeds.
    grant = answer.get("grant")
    installation_id = answer.get("installationId")
    return {
        "token": token,
        "grant": grant if is_printable_word(grant) else "?",
        "installationId": str(installation_id) if type(installation_id) is int else "?",
    }


def output_block(answer: Mapping[str, str]) -> str:
    """The GITHUB_OUTPUT block, written in one piece.

    `mint_app_token.run` masks the token before it writes this block. The grant
    and the installation id are display-only. `success` is the
    non-secret flag that callers test in the `if:` of later steps. It is
    the last line, so it appears only after the token line is complete.
    """
    return (
        f"token={answer['token']}\n"
        f"installation-id={answer['installationId']}\n"
        f"grant={answer['grant']}\n"
        "success=true\n"
    )
