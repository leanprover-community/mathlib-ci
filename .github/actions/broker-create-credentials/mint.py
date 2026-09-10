"""Exchange the job's GitHub OIDC token at a credential broker for
short-lived S3-compatible credentials, and expose them as step outputs.

The transport is injectable (`fetch`), so the test suite drives every
policy path without a network. Stdlib only: the action runs with the
runner's `python3` and installs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping

# A credential must be a single line from a strict charset before it
# reaches `::add-mask::` (which masks one line) or GITHUB_OUTPUT (where
# an embedded newline could define arbitrary outputs). The broker mints
# base64- and URL-safe material only.
CREDENTIAL_RE = re.compile(r"[A-Za-z0-9+/=._-]+")

# The broker URL is the credential endpoint the transport POSTs to, so
# it must be one plain https URL: no whitespace, no control characters,
# no query string, no trailing slash.
URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~%-]+)*")

# The audience is appended to the OIDC token endpoint's query string.
AUDIENCE_RE = re.compile(r"[A-Za-z0-9._:/-]+")

ATTEMPTS = 3
TIMEOUT_SECONDS = 30

# The tail of every warn-and-skip message: what the caller sees next.
SKIP_NOTE = "no credential output, so the steps gated on minted skip"

# Cloudflare's browser integrity check in front of the broker answers 403
# (error 1010) to Python's default `Python-urllib/x.y` agent. A named
# agent passes, and names the caller in the broker's logs.
USER_AGENT = "mathlib-ci/broker-create-credentials"

# The credential fields, in order. `sessionToken` is deliberately
# required: the broker always mints one, and its absence marks a
# malformed or foreign answer. A static-keypair grant would need this
# relaxed.
CREDENTIAL_FIELDS = ("accessKeyId", "secretAccessKey", "sessionToken")


class MintError(Exception):
    """A mint failure, with a one-line operator-facing message."""


Fetch = Callable[..., str]


def fetch_text(url: str, bearer: str, method: str = "GET", sleep: Callable[[float], None] = time.sleep) -> str:
    """Fetch the URL with a bearer token and return the response body.

    Transport failures and 5xx statuses retry with backoff: both
    requests this module makes are idempotent, so a retry is safe. A 4xx
    is a verdict on the request, so it raises at once.
    """
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(
                url,
                method=method,
                headers={"Authorization": f"Bearer {bearer}", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 or attempt + 1 == ATTEMPTS:
                raise
            sleep(2**attempt)
        except (urllib.error.URLError, OSError):
            if attempt + 1 == ATTEMPTS:
                raise
            sleep(2**attempt)
    raise AssertionError("unreachable: the loop returns or raises")


def audience_url(request_url: str, audience: str) -> str:
    """The OIDC token endpoint with the audience selector appended."""
    separator = "&" if "?" in request_url else "?"
    return f"{request_url}{separator}audience={audience}"


def parse_credentials(body: str) -> dict[str, str]:
    """Validate the broker's answer field by field.

    A 200 with a malformed body must take the same failure path as a
    transport error, and no field may reach the caller without passing
    the credential charset.
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
        if not isinstance(value, str) or not CREDENTIAL_RE.fullmatch(value):
            raise MintError("the broker answer carried no well-formed credential")
        credentials[field] = value
    # The grant name is display-only; a missing or malformed one must
    # not fail a mint that already produced good credentials.
    grant = answer.get("grant")
    if not isinstance(grant, str) or not CREDENTIAL_RE.fullmatch(grant):
        grant = "?"
    credentials["grant"] = grant
    return credentials


def output_block(credentials: Mapping[str, str]) -> str:
    """The GITHUB_OUTPUT block, written in one piece.

    The credential values are masked before this block is written; the
    grant is display-only. `minted` is the non-secret flag callers gate
    later steps on; testing a masked output's presence in an `if:` works
    but reads poorly. It sits last, so a truncated write leaves no flag
    rather than a flag over a partial credential.
    """
    return (
        f"access-key-id={credentials['accessKeyId']}\n"
        f"secret-access-key={credentials['secretAccessKey']}\n"
        f"session-token={credentials['sessionToken']}\n"
        f"grant={credentials['grant']}\n"
        "minted=true\n"
    )


def obtain(args: argparse.Namespace, env: Mapping[str, str], fetch: Fetch) -> dict[str, str]:
    """Run the two-request mint and return validated credentials."""
    if not URL_RE.fullmatch(args.broker_url):
        raise MintError("broker-url is not one plain https URL")
    if not AUDIENCE_RE.fullmatch(args.audience):
        raise MintError("audience carries characters outside its charset")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    if not request_token or not request_url:
        raise MintError("the job has no OIDC token endpoint (id-token: write missing?)")
    try:
        token_body = fetch(audience_url(request_url, args.audience), request_token)
    except urllib.error.HTTPError as error:
        raise MintError(f"the GitHub OIDC token endpoint answered HTTP {error.code}") from None
    except Exception:
        raise MintError("could not obtain the GitHub OIDC token") from None
    try:
        oidc_token = json.loads(token_body).get("value")
    except (ValueError, AttributeError):
        oidc_token = None
    if not isinstance(oidc_token, str) or not oidc_token:
        raise MintError("the OIDC token response carried no value")
    try:
        credentials_body = fetch(args.broker_url, oidc_token, method="POST")
    except urllib.error.HTTPError as error:
        raise MintError(f"the broker answered HTTP {error.code}") from None
    except Exception:
        raise MintError("the broker did not answer with credentials") from None
    return parse_credentials(credentials_body)


def run(argv: list[str] | None = None, env: Mapping[str, str] | None = None, fetch: Fetch = fetch_text, out=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--on-failure", required=True, choices=("warn-and-skip", "fail"))
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out

    try:
        credentials = obtain(args, env, fetch)
    except MintError as error:
        # In the warn-and-skip posture the step sets no outputs and
        # exits 0, so the caller's fallback path carries the run.
        if args.on_failure == "fail":
            print(f"::error::{error}", file=out)
            return 1
        print(f"::warning::{error}; {SKIP_NOTE}", file=out)
        return 0

    # Mask before any other output can carry a credential value.
    for field in CREDENTIAL_FIELDS:
        print(f"::add-mask::{credentials[field]}", file=out)
    out.flush()
    try:
        with open(args.github_output, "a", encoding="utf-8") as github_output:
            github_output.write(output_block(credentials))
    except OSError as error:
        # A runner fault, not a broker fault, but the posture still
        # decides: the caller's fallback must not depend on which side
        # failed. `minted` is the last line, so a partial write set no flag.
        message = f"could not write the step outputs ({error.strerror or error})"
        if args.on_failure == "fail":
            print(f"::error::{message}", file=out)
            return 1
        print(f"::warning::{message}; {SKIP_NOTE}", file=out)
        return 0
    print(f"credentials minted (grant: {credentials['grant']})", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(run())
