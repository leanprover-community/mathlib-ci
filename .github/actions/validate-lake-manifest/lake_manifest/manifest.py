"""The dependencies in a lake-manifest.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

FILE = "lake-manifest.json"
BRANCH_REVS = frozenset({"main", "master"})
TAG_REV = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?")
REPOSITORY_URL = re.compile(r"https://github\.com/(leanprover|leanprover-community)/([A-Za-z0-9._-]+)")
FULL_SHA = re.compile(r"[0-9a-f]{40}")


class ManifestError(Exception):
    """A lake-manifest.json that the step cannot read."""


@dataclass(frozen=True)
class Dependency:
    """One entry of `packages`. A field that is missing or not a string is `None`."""

    name: str | None
    url: str | None
    input_rev: str | None
    rev: str | None
    inherited: bool = False

    @property
    def tracks_branch(self) -> bool:
        return self.input_rev in BRANCH_REVS

    @property
    def tracks_tag(self) -> bool:
        return self.input_rev is not None and TAG_REV.fullmatch(self.input_rev) is not None

    @property
    def repository(self) -> tuple[str, str] | None:
        """The `(owner, repo)` of an allowed GitHub repository, else `None`."""
        match = REPOSITORY_URL.fullmatch(self.url) if self.url is not None else None
        return (match[1], match[2]) if match else None

    @property
    def pins_full_sha(self) -> bool:
        return self.rev is not None and FULL_SHA.fullmatch(self.rev) is not None


def parse(text: str) -> list[Dependency]:
    """The dependencies of a manifest, in manifest order."""
    try:
        packages = json.loads(text)["packages"]
    except (ValueError, TypeError, KeyError):
        raise ManifestError("lake-manifest.json has no `packages` list.") from None
    if not isinstance(packages, list) or not all(isinstance(p, dict) for p in packages):
        raise ManifestError("lake-manifest.json has no `packages` list.")

    def text_field(package: dict, key: str) -> str | None:
        value = package.get(key)
        return value if isinstance(value, str) else None

    return [
        Dependency(
            name=text_field(p, "name"),
            url=text_field(p, "url"),
            input_rev=text_field(p, "inputRev"),
            rev=text_field(p, "rev"),
            inherited=p.get("inherited") is True,
        )
        for p in packages
    ]
