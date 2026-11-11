"""The GitHub REST calls that the validation makes. Stdlib only.

Every request is a GET, so a retry is safe. `retry_delay` decides which
answers retry. A request fails after `len(BACKOFF) + 1` attempts.
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

BACKOFF = (1, 2, 4)  # Seconds before each retry.
MAX_RETRY_AFTER = 60  # The longest `Retry-After` that the request waits for.
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


def retry_delay(error: urllib.error.HTTPError, backoff: int) -> float | None:
    """The seconds to wait before a retry of the request that failed with `error`, or `None` for no retry.

    A 5xx, 408 or 429 status is transient. GitHub sends a rate limit as 403 or
    429, with `Retry-After` when a short wait is enough. Without that header, a
    403 is a missing permission or a rate limit that resets too late to wait for.
    """
    retry_after = error.headers.get("Retry-After", "") if error.headers else ""
    if error.code in (403, 429) and retry_after.isdigit():
        return max(int(retry_after), backoff) if int(retry_after) <= MAX_RETRY_AFTER else None
    return backoff if error.code >= 500 or error.code in (408, 429) else None


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
        # `None` marks the last attempt, which returns or raises.
        for backoff in (*BACKOFF, None):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                delay = None if backoff is None else retry_delay(error, backoff)
                if delay is None:
                    raise ApiError(f"GET {path} failed with HTTP {error.code}", error.code) from None
            except TRANSPORT_ERRORS:
                if backoff is None:
                    raise ApiError(f"GET {path} failed") from None
                delay = backoff
            time.sleep(delay)

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
