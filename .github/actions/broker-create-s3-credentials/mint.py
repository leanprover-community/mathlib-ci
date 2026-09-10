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
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

ATTEMPTS = 3
TIMEOUT_SECONDS = 30

# Every warn-and-skip warning ends with this clause, which states the
# consequence for the caller.
SKIP_NOTE = "no credential output, so the steps gated on minted skip"

# A Cloudflare browser integrity check in front of the broker answers 403
# (error 1010) to Python's default `Python-urllib/x.y` agent. A named
# agent passes and identifies the caller in the broker's logs.
USER_AGENT = "mathlib-ci/broker-create-s3-credentials"

# The credential fields, in order. `sessionToken` is required: the
# broker always mints one, and its absence marks a malformed or foreign
# answer.
CREDENTIAL_FIELDS = ("accessKeyId", "secretAccessKey", "sessionToken")


class MintError(Exception):
    """A mint failure, with a one-line operator-facing message."""


Fetch = Callable[..., str]


def is_token(value: object) -> bool:
    """Whether the value is one printable token: non-empty, no whitespace,
    no control characters.

    `::add-mask::` masks one line, and a `name=value` line in
    GITHUB_OUTPUT carries one value, so a credential must be a token
    before it reaches either.
    """
    return isinstance(value, str) and value != "" and value.isprintable() and not any(c.isspace() for c in value)


def check_endpoint(url: str) -> None:
    """Reject a broker URL that is not one https URL with a host.

    The OIDC token travels as a bearer to this URL, so the scheme must be
    https. `urlsplit` drops tab and newline characters, so the token
    check runs first.
    """
    if not is_token(url):
        raise MintError("broker-url is not one https URL")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != "https" or not parts.hostname or port == -1:
        raise MintError("broker-url is not one https URL")


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
    """The OIDC token endpoint with the audience selector appended, URL-encoded."""
    separator = "&" if "?" in request_url else "?"
    return f"{request_url}{separator}{urlencode({'audience': audience})}"


def parse_credentials(body: str) -> dict[str, str]:
    """Validate the broker's answer field by field.

    A 200 with a malformed body takes the same failure path as a
    transport error. Every field is one printable token before it
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
        if not is_token(value):
            raise MintError("the broker answer carried no well-formed credential")
        credentials[field] = value
    # The grant name is display-only. A missing or malformed grant prints
    # as `?` and the mint succeeds.
    grant = answer.get("grant")
    credentials["grant"] = grant if is_token(grant) else "?"
    return credentials


def output_block(credentials: Mapping[str, str]) -> str:
    """The GITHUB_OUTPUT block, written in one piece.

    `run` masks the credential values before it writes this block. The
    grant is display-only. `minted` is the non-secret flag that callers
    gate later steps on. It is the last line, so a truncated write leaves
    no flag over a partial credential.
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
    check_endpoint(args.broker_url)
    if not args.audience:
        raise MintError("audience is empty")
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
        # A runner fault, not a broker fault. The posture decides here
        # too, so the caller's fallback does not depend on which side
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
