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
from typing import Any, Callable
from urllib.parse import quote

from .manifest import ManifestError

ATTEMPTS = 4
TIMEOUT_SECONDS = 30
USER_AGENT = "mathlib-ci/validate-lake-manifest"
# What one HTTP request can raise. `OSError` covers `urllib.error.URLError`
# and its subclass `HTTPError`; `http.client.HTTPException` covers
# `IncompleteRead` on a truncated body.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class ApiError(Exception):
    """A GitHub API request that failed. `status` is `None` for a transport fault."""

    def __init__(self, path: str, status: int | None):
        super().__init__(f"GET {path} failed" + (f" with HTTP {status}" if status else ""))
        self.status = status


def is_transient(status: int) -> bool:
    return status >= 500 or status in (408, 429)


class GitHub:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.urlopen = urlopen
        self.sleep = sleep

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
                with self.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if not is_transient(error.code) or last:
                    raise ApiError(path, error.code) from None
            except TRANSPORT_ERRORS:
                if last:
                    raise ApiError(path, None) from None
            self.sleep(2**attempt)
        raise AssertionError("unreachable: the loop returns or raises")

    def compare(self, repository: str, base: str, head: str) -> dict:
        """The comparison of `base...head`, with at most one commit listed."""
        return self.get(f"/repos/{repository}/compare/{quote(base, safe='')}...{quote(head, safe='')}?per_page=1")

    def read_manifest(self, repository: str, ref: str) -> str:
        """The text of lake-manifest.json at `ref`. Nothing lands on disk."""
        answer = self.get(f"/repos/{repository}/contents/lake-manifest.json?ref={quote(ref, safe='')}")
        if not isinstance(answer, dict) or answer.get("type") != "file":
            kind = answer.get("type") if isinstance(answer, dict) else "directory"
            raise ManifestError(f"lake-manifest.json at `{ref}` is a {kind}, not a file.")
        return base64.b64decode(answer["content"]).decode("utf-8")

    def is_ancestor(self, owner: str, repo: str, commit: str, tip: str) -> bool:
        try:
            status = self.compare(f"{owner}/{repo}", commit, tip)["status"]
        except ApiError as error:
            if error.status == 404:
                return False
            raise
        return status in ("ahead", "identical")

    def tag_commit(self, owner: str, repo: str, tag: str) -> str | None:
        # The commits endpoint peels an annotated tag to its commit.
        try:
            return self.get(f"/repos/{owner}/{repo}/commits/{quote('refs/tags/' + tag, safe='/')}")["sha"]
        except ApiError as error:
            if error.status in (404, 422):
                return None
            raise
