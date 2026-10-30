"""Validate lake-manifest.json at a commit and report the findings as annotations.

A field finding is an error. A pin finding is an error with `--strict`
and a warning otherwise. With `--allow-older-pins`, an older pin is a
warning on every run. Exits 1 on an error or when the manifest cannot
be read.
"""

from __future__ import annotations

import argparse
import os
import sys

from .github import ApiError, GitHub
from .validate import FIELD, FILE, OLDER_PIN, Finding, validate

OVERRIDE_VARIABLE = "MATHLIB_ALLOW_OLDER_DEPENDENCY_PINS"
STRICT_NOTE = "Note: This check fails the Build job on bors runs."
ADMIN_NOTE = f"Note: For an intentional revert, a repository admin can set the variable `{OVERRIDE_VARIABLE}` to `true`."
OVERRIDE_NOTE = f"Note: The variable `{OVERRIDE_VARIABLE}` is `true`, so this check only warns."


def annotate(finding: Finding, strict: bool, allow_older_pins: bool) -> tuple[str, str]:
    """The level of a finding and the message of its annotation."""
    if finding.kind == FIELD:
        return "error", "\n".join((finding.problem, *finding.advice))
    if finding.kind == OLDER_PIN and allow_older_pins:
        return "warning", "\n".join((finding.problem, OVERRIDE_NOTE))
    notes = (STRICT_NOTE, ADMIN_NOTE) if finding.kind == OLDER_PIN else (STRICT_NOTE,)
    return "error" if strict else "warning", "\n".join((finding.problem, *notes, *finding.advice))


def command(level: str, title: str, message: str) -> str:
    """The workflow command, with GitHub's escaping for properties and data."""
    return f"::{level} file={escape(FILE, in_property=True)},title={escape(title, in_property=True)}::{escape(message)}"


def escape(value: str, in_property: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if in_property else value


def main() -> int:
    parser = argparse.ArgumentParser(prog="python3 -m lake_manifest", description=__doc__)
    parser.add_argument("--repository", required=True, help="The `owner/repo` that holds the commit.")
    parser.add_argument("--ref", required=True, help="The commit to check.")
    parser.add_argument("--strict", action="store_true", help="Pin findings are errors, not warnings.")
    parser.add_argument("--allow-older-pins", action="store_true", help="An older pin is a warning on every run.")
    args = parser.parse_args()
    github = GitHub(os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    try:
        findings = validate(github, args.repository, args.ref)
    except (ApiError, ValueError) as error:
        print(f"::error::CI could not read {FILE}: {error}")
        return 1
    failed = False
    for finding in findings:
        level, message = annotate(finding, args.strict, args.allow_older_pins)
        print(command(level, finding.title, message))
        failed |= level == "error"
    if failed:
        print(f"::error::{FILE} is not valid.")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
