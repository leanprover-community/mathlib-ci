"""The GitHub REST calls that the validation makes. Stdlib only.

Every request is a GET, so a retry is safe. A transport fault or a
transient status (any 5xx, 408 or 429) retries with backoff. Any other
status raises `ApiError` at once.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

ATTEMPTS = 4
TIMEOUT_SECONDS = 30
USER_AGENT = "mathlib-ci/validate-lake-manifest"
# `OSError` covers `urllib.error.URLError`; `http.client.HTTPException`
# covers `IncompleteRead` on a truncated body.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class ApiError(Exception):
    """A request that failed, or an answer the validation cannot use. `status` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def is_transient(status: int) -> bool:
    return status >= 500 or status in (408, 429)


class GitHub:
    def __init__(self, token: str, api_url: str):
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path: str) -> Any:
        """The decoded JSON answer to `GET path`."""
        request = urllib.request.Request(
            self.api_url + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        for attempt in range(ATTEMPTS):
            last = attempt + 1 == ATTEMPTS
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if not is_transient(error.code) or last:
                    raise ApiError(f"GET {path} failed with HTTP {error.code}", error.code) from None
            except TRANSPORT_ERRORS:
                if last:
                    raise ApiError(f"GET {path} failed") from None
            time.sleep(2**attempt)
        raise AssertionError("unreachable: the loop returns or raises")

    def compare(self, repository: str, base: str, head: str) -> dict:
        return self.get(f"/repos/{repository}/compare/{quote(base, safe='')}...{quote(head, safe='')}?per_page=1")

    def merge_base(self, repository: str, base: str, head: str) -> str:
        return self.compare(repository, base, head)["merge_base_commit"]["sha"]

    def read_manifest(self, repository: str, ref: str) -> str:
        """The text of lake-manifest.json at `ref`."""
        answer = self.get(f"/repos/{repository}/contents/lake-manifest.json?ref={quote(ref, safe='')}")
        if not isinstance(answer, dict) or answer.get("type") != "file":
            raise ApiError(f"lake-manifest.json at `{ref}` is not a file.")
        return base64.b64decode(answer["content"]).decode("utf-8")

    def is_ancestor(self, repository: str, commit: str, tip: str) -> bool:
        """Whether `commit` is `tip` or an ancestor of `tip`. False for an unknown commit."""
        try:
            status = self.compare(repository, commit, tip)["status"]
        except ApiError as error:
            if error.status == 404:
                return False
            raise
        return status in ("ahead", "identical")

    def tag_commit(self, repository: str, tag: str) -> str | None:
        """The commit of `tag`, or `None` when the tag does not exist. An annotated tag is peeled."""
        try:
            return self.get(f"/repos/{repository}/commits/refs/tags/{quote(tag, safe='')}")["sha"]
        except ApiError as error:
            if error.status in (404, 422):
                return None
            raise
