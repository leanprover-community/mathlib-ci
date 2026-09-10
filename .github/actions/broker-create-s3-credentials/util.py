"""Building blocks for the mint in `mint.py`: the retrying bearer-token
transport, the input and answer checks, and the GITHUB_OUTPUT block.

Every function here is pure except `fetch_once`, which makes one HTTP
request.
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

# A Cloudflare browser integrity check in front of the broker answers 403
# (error 1010) to Python's default `Python-urllib/x.y` agent. A named
# agent passes and identifies the caller in the broker's logs.
USER_AGENT = "mathlib-ci/broker-create-s3-credentials"

# The credential fields, in order. `sessionToken` is required: the
# broker always mints one, and its absence marks a malformed or foreign
# answer.
CREDENTIAL_FIELDS = ("accessKeyId", "secretAccessKey", "sessionToken")

# What one HTTP request can raise. `OSError` covers `urllib.error.URLError`
# and its subclass `HTTPError`; `http.client.HTTPException` covers
# `IncompleteRead` on a truncated body.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class MintError(Exception):
    """A mint failure, with a one-line operator-facing message."""


# (url, bearer, method) -> response body. Raises one of TRANSPORT_ERRORS
# on failure; an `HTTPError` carries the status.
Fetch = Callable[[str, str, str], str]


def is_printable_word(value: object) -> bool:
    """Whether the value is one printable word: non-empty, no whitespace,
    no control characters.

    `::add-mask::` masks one line, and a `name=value` line in
    GITHUB_OUTPUT carries one value, so a credential must be one word
    before it reaches either.
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


def fetch_once(url: str, bearer: str, method: str) -> str:
    """One HTTP request with a bearer token; return the response body."""
    request = urllib.request.Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {bearer}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def with_retries(call: Callable[[], str], sleep: Callable[[float], None] | None = None) -> str:
    """Call up to ATTEMPTS times and return the first answer.

    Transport faults and 5xx statuses retry with backoff: both requests
    the mint makes are idempotent, so a retry is safe. A 4xx is a
    verdict on the request, so it raises at once. Any other exception is
    a bug and propagates untouched.
    """
    sleep = time.sleep if sleep is None else sleep
    for attempt in range(ATTEMPTS):
        last = attempt + 1 == ATTEMPTS
        try:
            return call()
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 or last:
                raise
        except TRANSPORT_ERRORS:
            if last:
                raise
        sleep(2**attempt)
    raise AssertionError("unreachable: the loop returns or raises")


def fetch_text(url: str, bearer: str, method: str) -> str:
    """The default transport: `fetch_once` under `with_retries`."""
    return with_retries(lambda: fetch_once(url, bearer, method))


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


def parse_credentials(body: str) -> dict[str, str]:
    """Validate the broker's answer field by field.

    A 200 with a malformed body takes the same failure path as a
    transport error. Every field is one printable word before it
    reaches the caller.
    """
    try:
        answer = json.loads(body)
    except ValueError:
        raise MintError("the broker answer was not JSON") from None
    if not isinstance(answer, dict):
        raise MintError("the broker answer was not a JSON object")
    credentials: dict[str, str] = {}
    for field in CREDENTIAL_FIELDS:
        value = answer.get(field)
        if not is_printable_word(value):
            raise MintError("the broker answer carried no well-formed credential")
        credentials[field] = value
    # The grant name is display-only. A missing or malformed grant prints
    # as `?` and the mint succeeds.
    grant = answer.get("grant")
    credentials["grant"] = grant if is_printable_word(grant) else "?"
    return credentials


def output_block(credentials: Mapping[str, str]) -> str:
    """The GITHUB_OUTPUT block, written in one piece.

    `mint.run` masks the credential values before it writes this block. The
    grant is display-only. `success` is the non-secret flag that callers
    test in the `if:` of later steps. It is the last line, so it appears
    only after every credential line is complete.
    """
    return (
        f"access-key-id={credentials['accessKeyId']}\n"
        f"secret-access-key={credentials['secretAccessKey']}\n"
        f"session-token={credentials['sessionToken']}\n"
        f"grant={credentials['grant']}\n"
        "success=true\n"
    )
