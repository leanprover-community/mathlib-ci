"""Run the action's CLI against a fake GitHub API, and read its annotations.

The fake serves the three endpoints the action uses from a `FakeGitHub` table:
the manifests by ref, the commits each branch or commit contains, and the
tags. `main` holds OLD and NEW; FORK is on no branch; UNKNOWN is no commit.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ACTION = Path(__file__).resolve().parents[2] / ".github" / "actions" / "validate-lake-manifest"
REPOSITORY = "leanprover-community/mathlib4"
TOKEN = "token"
HEAD = "head"
MERGE_BASE = "merge-base"
OLD = "1" * 40
NEW = "2" * 40
FORK = "3" * 40
TAG = "4" * 40
UNKNOWN = "9" * 40


def package(name="batteries", rev=NEW, input_rev="main", url=None, inherited=False) -> dict:
    url = f"https://github.com/leanprover-community/{name}" if url is None else url
    return {"name": name, "url": url, "inputRev": input_rev, "rev": rev, "inherited": inherited}


def manifest(*packages: dict) -> str:
    return json.dumps({"version": "1.1.0", "packages": list(packages)})


@dataclass
class FakeGitHub:
    """What the fake GitHub serves.

    A manifest is its text, or the answer to send instead: an HTTP status, or
    a JSON value such as `[]` for a directory. `failures` is how many requests
    to answer with 502 first.
    """

    head: object
    base: object = None
    tags: dict[str, str] = field(default_factory=dict)
    failures: int = 0

    CONTAINS = {"main": {OLD, NEW}, NEW: {OLD, NEW}, OLD: {OLD}, FORK: {FORK}}

    def manifest(self, ref: str):
        answer = {HEAD: self.head, MERGE_BASE: self.base}.get(ref)
        if isinstance(answer, str):
            return {"type": "file", "content": base64.b64encode(answer.encode()).decode()}
        return 404 if answer is None else answer

    def compare(self, base: str, head: str):
        if (base, head) == ("master", HEAD):  # mathlib4: the action wants the merge base.
            return {"status": "ahead", "merge_base_commit": {"sha": MERGE_BASE}}
        if base not in self.CONTAINS or head not in self.CONTAINS:
            return 404
        status = "identical" if base == head else "ahead" if base in self.CONTAINS[head] else "diverged"
        return {"status": status, "merge_base_commit": {"sha": base}}

    def tag_commit(self, tag: str):
        return {"sha": self.tags[tag]} if tag in self.tags else 404


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        github: FakeGitHub = self.server.github
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.answer(401)
        if github.failures:
            github.failures -= 1
            return self.answer(502)
        if path.endswith("/contents/lake-manifest.json"):
            return self.answer(github.manifest(urllib.parse.parse_qs(url.query)["ref"][0]))
        if match := re.fullmatch(r"/repos/[^/]+/[^/]+/compare/(.+)\.\.\.(.+)", path):
            return self.answer(github.compare(match[1], match[2]))
        if match := re.fullmatch(r"/repos/[^/]+/[^/]+/commits/refs/tags/(.+)", path):
            return self.answer(github.tag_commit(match[1]))
        self.answer(404)

    def answer(self, body):
        """Send `body` as JSON, or an int as an HTTP status."""
        if isinstance(body, int):
            return self.send_error(body)
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="session")
def server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture
def stdout(server):
    """Run the action against `github`: the exit code and what it printed."""

    def stdout(github: FakeGitHub, *flags: str) -> tuple[int, str]:
        server.github = github
        proc = subprocess.run(
            [sys.executable, "-m", "lake_manifest", "--repository", REPOSITORY, "--ref", HEAD, *flags],
            env={**os.environ, "PYTHONPATH": str(ACTION), "GITHUB_TOKEN": TOKEN, "GITHUB_API_URL": server.url},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.stderr == "", proc.stderr
        return proc.returncode, proc.stdout

    return stdout


COMMAND = re.compile(r"::(\w+)(?: file=lake-manifest\.json,title=([^:]*))?::(.*)")


def annotation(line: str) -> tuple[str, str]:
    """The level and title of a workflow command. A command without a title gives its message."""
    match = COMMAND.fullmatch(line)
    assert match, line
    level, title, message = match.groups()
    return level, message if title is None else title


@pytest.fixture
def run(stdout):
    """Run the action against `github`: the exit code and the `(level, title)` of each line."""

    def run(github: FakeGitHub, *flags: str) -> tuple[int, list[tuple[str, str]]]:
        code, out = stdout(github, *flags)
        return code, [annotation(line) for line in out.splitlines()]

    return run
