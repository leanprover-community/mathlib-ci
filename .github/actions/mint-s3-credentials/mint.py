"""Exchange the job's GitHub OIDC token at a credential broker for
short-lived S3-compatible credentials, and expose them as step outputs.

The step fails when the mint fails. `util.py` holds the building blocks.
The transport is injectable (`fetch`), so the test suite drives every
failure path offline. Stdlib only, so the action runs on the runner's
`python3`.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
from typing import Mapping, TextIO

from util import (
    CREDENTIAL_FIELDS,
    TRANSPORT_ERRORS,
    Fetch,
    MintError,
    check_broker_url,
    fetch_text,
    oidc_token_url,
    output_block,
    parse_credentials,
    parse_oidc_token,
)


def mint_credentials(broker_url: str, audience: str, env: Mapping[str, str], fetch: Fetch) -> dict[str, str]:
    """Run the two-request mint and return validated credentials.

    Every mint failure raises `MintError`. A transport fault is a mint
    failure. Any other exception from `fetch` is a bug and propagates.
    """
    check_broker_url(broker_url)
    if not audience:
        raise MintError("audience is empty")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    if not request_token or not request_url:
        raise MintError("the job has no OIDC token endpoint (id-token: write missing?)")
    try:
        token_body = fetch(oidc_token_url(request_url, audience), request_token, "GET")
    except urllib.error.HTTPError as error:
        raise MintError(f"the GitHub OIDC token endpoint answered HTTP {error.code}") from None
    except TRANSPORT_ERRORS:
        raise MintError("could not obtain the GitHub OIDC token") from None
    oidc_token = parse_oidc_token(token_body)
    try:
        credentials_body = fetch(broker_url, oidc_token, "POST")
    except urllib.error.HTTPError as error:
        raise MintError(f"the broker answered HTTP {error.code}") from None
    except TRANSPORT_ERRORS:
        raise MintError("the broker did not answer with credentials") from None
    return parse_credentials(credentials_body)


def run(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    fetch: Fetch = fetch_text,
    out: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out

    try:
        credentials = mint_credentials(args.broker_url, args.audience, env, fetch)
    except MintError as error:
        print(f"::error::{error}", file=out)
        return 1

    # Mask before any other output can carry a credential value.
    for field in CREDENTIAL_FIELDS:
        print(f"::add-mask::{credentials[field]}", file=out)
    out.flush()
    try:
        with open(args.github_output, "a", encoding="utf-8") as github_output:
            github_output.write(output_block(credentials))
    except OSError as error:
        # A partial write stops before the `success` line.
        print(f"::error::could not write the step outputs ({error.strerror or error})", file=out)
        return 1
    print(f"credentials minted (grant: {credentials['grant']})", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(run())
