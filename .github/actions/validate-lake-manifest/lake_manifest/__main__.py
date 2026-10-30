"""Validate lake-manifest.json at a commit and report the findings as annotations.

Exits 1 when a finding is an error or the manifest cannot be read.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Mapping, TextIO

from .github import ApiError, GitHub
from .manifest import ManifestError
from .report import Policy, annotate
from .validate import Client, validate


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: Client | None = None,
    out: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lake_manifest", description=__doc__)
    parser.add_argument("--repository", required=True, help="The `owner/repo` that holds the commit.")
    parser.add_argument("--ref", required=True, help="The commit to check.")
    parser.add_argument("--strict", action="store_true", help="Pin findings are errors, not warnings.")
    parser.add_argument("--allow-older-pins", action="store_true", help="An older pin is a warning on every run.")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    if client is None:
        client = GitHub(env["GITHUB_TOKEN"], env.get("GITHUB_API_URL", "https://api.github.com"))

    try:
        findings = validate(client, args.repository, args.ref)
    except (ApiError, ManifestError) as error:
        print(f"::error::CI could not read lake-manifest.json: {error}", file=out)
        return 1
    policy = Policy(strict=args.strict, allow_older_pins=args.allow_older_pins)
    annotations = [annotate(finding, policy) for finding in findings]
    for annotation in annotations:
        print(annotation.command(), file=out)
    if any(annotation.level == "error" for annotation in annotations):
        print("::error::lake-manifest.json is not valid.", file=out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
