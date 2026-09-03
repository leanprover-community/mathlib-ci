"""Exchange the job's GitHub OIDC token at the mathlib cache broker for
short-lived S3-compatible credentials, and export them for the cache tool.

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
# reaches `::add-mask::` (which masks one line) or GITHUB_ENV (where an
# embedded newline could define arbitrary variables). The broker mints
# base64- and URL-safe material only.
CREDENTIAL_RE = re.compile(r"[A-Za-z0-9+/=._-]+")

# The URL inputs reach GITHUB_ENV and the transport layer, so each must
# be one plain https URL: no whitespace, no control characters, no
# query string, no trailing slash.
URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~%-]+)*")

# The audience is appended to the OIDC token endpoint's query string.
AUDIENCE_RE = re.compile(r"[A-Za-z0-9._:/-]+")

ATTEMPTS = 3
TIMEOUT_SECONDS = 30

# The mint and export paths, in order. `sessionToken` is deliberately
# required: the broker always mints one, and its absence marks a
# malformed or foreign answer. A static-keypair grant would need this
# relaxed.
CREDENTIAL_FIELDS = ("accessKeyId", "secretAccessKey", "sessionToken")


class MintError(Exception):
    """A mint failure, with a one-line operator-facing message."""


Fetch = Callable[..., str]


def fetch_text(url: str, bearer: str, method: str = "GET", sleep: Callable[[float], None] = time.sleep) -> str:
    """Fetch the URL with a bearer token and return the response body.

    Transport failures and error statuses retry with backoff: both
    requests this module makes are idempotent, so a retry is safe.
    """
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(
                url, method=method, headers={"Authorization": f"Bearer {bearer}"}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
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


def export_block(credentials: Mapping[str, str], put_base_url: str) -> str:
    """The GITHUB_ENV block, written in one piece.

    The put base sits after the credentials: the cache tool routes the
    write to the bucket only on MATHLIB_CACHE_PUT_BASE_URL, so a
    truncated write leaves the credentials without a bucket destination
    rather than a foreign credential aimed at the bucket. MINTED is the
    non-secret sentinel callers gate later steps on; testing a masked
    credential's presence in an `if:` works but reads poorly.
    """
    return (
        f"MATHLIB_CACHE_S3_ACCESS_KEY_ID={credentials['accessKeyId']}\n"
        f"MATHLIB_CACHE_S3_SECRET_ACCESS_KEY={credentials['secretAccessKey']}\n"
        f"MATHLIB_CACHE_S3_SESSION_TOKEN={credentials['sessionToken']}\n"
        f"MATHLIB_CACHE_PUT_BASE_URL={put_base_url}\n"
        "MATHLIB_CACHE_DEVELOPER_MINTED=true\n"
    )


def obtain(args: argparse.Namespace, env: Mapping[str, str], fetch: Fetch) -> dict[str, str]:
    """Run the two-request mint and return validated credentials."""
    for name, value in (("broker-url", args.broker_url), ("put-base-url", args.put_base_url)):
        if not URL_RE.fullmatch(value):
            raise MintError(f"{name} is not one plain https URL")
    if not AUDIENCE_RE.fullmatch(args.audience):
        raise MintError("audience carries characters outside its charset")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    if not request_token or not request_url:
        raise MintError("the job has no OIDC token endpoint (id-token: write missing?)")
    try:
        token_body = fetch(audience_url(request_url, args.audience), request_token)
    except Exception:
        raise MintError("could not obtain the GitHub OIDC token") from None
    try:
        oidc_token = json.loads(token_body).get("value")
    except (ValueError, AttributeError):
        oidc_token = None
    if not isinstance(oidc_token, str) or not oidc_token:
        raise MintError("the OIDC token response carried no value")
    try:
        credentials_body = fetch(f"{args.broker_url}/r2-credentials", oidc_token, method="POST")
    except Exception:
        raise MintError("the cache broker did not answer with credentials") from None
    return parse_credentials(credentials_body)


def run(argv: list[str] | None = None, env: Mapping[str, str] | None = None, fetch: Fetch = fetch_text, out=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-url", required=True)
    parser.add_argument("--put-base-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--on-failure", required=True, choices=("warn-and-skip", "fail"))
    parser.add_argument("--github-env", required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out

    try:
        credentials = obtain(args, env, fetch)
    except MintError as error:
        # In the warn-and-skip posture the step exports nothing and
        # exits 0, so the caller's other upload path carries the run.
        if args.on_failure == "fail":
            print(f"::error::{error}", file=out)
            return 1
        print(
            f"::warning::{error}; no credentials exported, the broker-backed upload will be skipped",
            file=out,
        )
        return 0

    # Mask before any other output can carry a credential value.
    for field in CREDENTIAL_FIELDS:
        print(f"::add-mask::{credentials[field]}", file=out)
    out.flush()
    with open(args.github_env, "a", encoding="utf-8") as github_env:
        github_env.write(export_block(credentials, args.put_base_url))
    print(f"cache credentials minted (grant: {credentials['grant']})", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(run())
