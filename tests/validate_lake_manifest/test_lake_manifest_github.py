"""The REST calls in `github.py`, driven through a fake `urlopen`."""

from __future__ import annotations

import base64
import http.client
import io
import json
import urllib.error

import pytest

from lake_manifest.github import ATTEMPTS, ApiError, GitHub
from lake_manifest.manifest import ManifestError


class FakeUrlopen:
    """Answers each request with the next item: a JSON value, or an exception to raise."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls: list[str] = []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", {}, io.BytesIO(b""))


def client(*answers) -> tuple[GitHub, FakeUrlopen, list[float]]:
    urlopen, sleeps = FakeUrlopen(*answers), []
    return GitHub("token", urlopen=urlopen, sleep=sleeps.append), urlopen, sleeps


@pytest.mark.parametrize("fault", [http_error(502), http_error(429), urllib.error.URLError("down"),
                                   TimeoutError(), http.client.IncompleteRead(b"")])
def test_transient_faults_retry(fault):
    github, _, sleeps = client(fault, {"ok": 1})
    assert github.get("/x") == {"ok": 1}
    assert sleeps == [1]


def test_retries_stop_after_the_last_attempt():
    github, urlopen, sleeps = client(*[http_error(503)] * ATTEMPTS)
    with pytest.raises(ApiError) as raised:
        github.get("/x")
    assert raised.value.status == 503
    assert len(urlopen.urls) == ATTEMPTS
    assert sleeps == [1, 2, 4]


def test_a_verdict_raises_at_once():
    github, urlopen, _ = client(http_error(403))
    with pytest.raises(ApiError, match="GET /x failed with HTTP 403"):
        github.get("/x")
    assert len(urlopen.urls) == 1


def test_read_manifest_decodes_a_file_and_quotes_the_ref():
    content = base64.b64encode(b'{"packages": []}').decode()
    github, urlopen, _ = client({"type": "file", "content": content})
    assert github.read_manifest("o/r", "a/b") == '{"packages": []}'
    assert urlopen.urls == ["https://api.github.com/repos/o/r/contents/lake-manifest.json?ref=a%2Fb"]


@pytest.mark.parametrize("answer, kind", [({"type": "symlink", "target": "/etc"}, "symlink"), ([], "directory")])
def test_read_manifest_rejects_a_non_file(answer, kind):
    github, _, _ = client(answer)
    with pytest.raises(ManifestError, match=f"is a {kind}, not a file"):
        github.read_manifest("o/r", "sha")


@pytest.mark.parametrize("answer, expected", [
    ({"status": "ahead"}, True),
    ({"status": "identical"}, True),
    ({"status": "behind"}, False),
    ({"status": "diverged"}, False),
    (http_error(404), False),
])
def test_is_ancestor(answer, expected):
    github, urlopen, _ = client(answer)
    assert github.is_ancestor("o", "r", "commit", "main") is expected
    assert urlopen.urls == ["https://api.github.com/repos/o/r/compare/commit...main?per_page=1"]


def test_is_ancestor_raises_on_other_errors():
    github, _, _ = client(http_error(403))
    with pytest.raises(ApiError):
        github.is_ancestor("o", "r", "commit", "main")


@pytest.mark.parametrize("answer, expected", [({"sha": "abc"}, "abc"), (http_error(404), None), (http_error(422), None)])
def test_tag_commit(answer, expected):
    github, urlopen, _ = client(answer)
    assert github.tag_commit("o", "r", "v1.0.0") == expected
    assert urlopen.urls == ["https://api.github.com/repos/o/r/commits/refs/tags/v1.0.0"]
