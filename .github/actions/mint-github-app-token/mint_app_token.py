"""Exchange the job's GitHub OIDC token at the GitHub App broker for a
short-lived installation token of one app, and expose it as a step
output.

The step fails when the mint fails. `app_token_util.py` holds the building blocks.
The transport is injectable (`fetch`), so the test suite drives every
failure path offline. Stdlib only, so the action runs on the runner's
`python3`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from typing import Mapping, TextIO

from app_token_util import (
    TRANSPORT_ERRORS,
    Fetch,
    MintError,
    body_excerpt,
    check_broker_url,
    fetch_text,
    is_printable_word,
    oidc_token_url,
    output_block,
    parse_oidc_token,
    parse_token,
)


def mint_token(broker_url: str, audience: str, app: str, env: Mapping[str, str], fetch: Fetch) -> dict[str, str]:
    """Run the two-request mint and return the validated token answer.

    Every mint failure raises `MintError`. A transport fault is a mint
    failure. Any other exception from `fetch` is a bug and propagates.
    """
    check_broker_url(broker_url)
    if not audience:
        raise MintError("audience is empty")
    if not is_printable_word(app):
        raise MintError("app is not one app name")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    if not request_token or not request_url:
        raise MintError("the job has no OIDC token endpoint (id-token: write missing?)")
    try:
        token_body = fetch(oidc_token_url(request_url, audience), request_token, "GET", None)
    except urllib.error.HTTPError as error:
        raise MintError(f"the GitHub OIDC token endpoint answered HTTP {error.code}") from None
    except TRANSPORT_ERRORS:
        raise MintError("could not obtain the GitHub OIDC token") from None
    oidc_token = parse_oidc_token(token_body)
    try:
        answer_body = fetch(broker_url, oidc_token, "POST", json.dumps({"app": app}))
    except urllib.error.HTTPError as error:
        # The broker's error body names the app and the unmatched claims
        # and carries no secret, so it is safe to log for diagnostics.
        detail = body_excerpt(error)
        raise MintError(f"the broker answered HTTP {error.code}" + (f": {detail}" if detail else "")) from None
    except TRANSPORT_ERRORS:
        raise MintError("the broker did not answer with a token") from None
    return parse_token(answer_body)


def run(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    fetch: Fetch = fetch_text,
    out: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out

    try:
        answer = mint_token(args.broker_url, args.audience, args.app, env, fetch)
    except MintError as error:
        print(f"::error::{error}", file=out)
        return 1

    # Mask before any other output can carry the token.
    print(f"::add-mask::{answer['token']}", file=out)
    out.flush()
    try:
        with open(args.github_output, "a", encoding="utf-8") as github_output:
            github_output.write(output_block(answer))
    except OSError as error:
        # A partial write stops before the `success` line.
        print(f"::error::could not write the step outputs ({error.strerror or error})", file=out)
        return 1
    print(f"token minted (grant: {answer['grant']}, installation: {answer['installationId']})", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(run())
