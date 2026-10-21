"""The building blocks in `app_token_util.py`, each on its own.

Layout: the word and broker URL checks, the OIDC token URL, the two
answer parsers, the output block, then the retry policy, the error body
excerpt, and the one urllib-facing request.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

import app_token_util as util

OIDC_ANSWER = json.dumps({"value": "jwt"})
GOOD_ANSWER = {
    "grant": "nolints",
    "app": "mathlib-nolints",
    "installationId": 1001,
    "token": "ghs_abc.123-x_y",
    "expiresAt": "2026-10-21T13:00:00Z",
    "permissions": {"contents": "write"},
}
# `parse_token` keeps the three fields the step outputs, as text.
PARSED = {"token": "ghs_abc.123-x_y", "grant": "nolints", "installationId": "1001"}


def http_error(code, body=None):
    return urllib.error.HTTPError("https://x.example", code, "status", {}, body)


def no_sleep(_seconds):
    pass


class TestIsPrintableWord:
    @pytest.mark.parametrize("value", ["ghs_abc", "secret/mock+1=", "sess.token_a-b", "tökén"])
    def test_accepts_a_printable_word(self, value):
        assert util.is_printable_word(value)

    @pytest.mark.parametrize(
        "value",
        ["", "two words", "tab\there", "line\nbreak", "cr\rhere", "\x1b[31mred", "sep line", None, 7],
        ids=["empty", "space", "tab", "newline", "cr", "escape", "line-separator", "none", "int"],
    )
    def test_rejects_everything_else(self, value):
        assert not util.is_printable_word(value)


class TestCheckBrokerUrl:
    @pytest.mark.parametrize("url", ["https://x.example/github-app-token", "https://x.example:8443/a/b", "https://x.example"])
    def test_accepts_an_https_url_with_a_host(self, url):
        util.check_broker_url(url)

    @pytest.mark.parametrize(
        "url",
        ["https://x.example\nEVIL=1", "http://x.example", "https://x.example/path ", "https:///path", "https://x.example:99999/p", "x.example/p"],
        ids=["newline", "not-https", "trailing-space", "no-host", "bad-port", "no-scheme"],
    )
    def test_rejects_the_rest(self, url):
        with pytest.raises(util.MintError, match="broker-url is not one https URL"):
            util.check_broker_url(url)


class TestOidcTokenUrl:
    def test_appends_to_an_existing_query_string(self):
        assert util.oidc_token_url("https://x/token?v=2", "aud") == "https://x/token?v=2&audience=aud"

    def test_starts_the_query_string_when_none_exists(self):
        assert util.oidc_token_url("https://x/token", "aud") == "https://x/token?audience=aud"

    def test_url_encodes_the_audience(self):
        assert util.oidc_token_url("https://x/token", "a b&c=1/d") == "https://x/token?audience=a+b%26c%3D1%2Fd"


class TestParseOidcToken:
    def test_returns_the_value(self):
        assert util.parse_oidc_token(OIDC_ANSWER) == "jwt"

    @pytest.mark.parametrize(
        "body",
        ["<html>oops</html>", "{}", "[]", "null", json.dumps({"value": ""}), json.dumps({"value": 5})],
        ids=["not-json", "no-value", "list", "null", "empty-value", "not-a-string"],
    )
    def test_rejects_an_answer_without_a_token(self, body):
        with pytest.raises(util.MintError, match="the OIDC token response carried no value"):
            util.parse_oidc_token(body)


class TestParseToken:
    def test_accepts_the_broker_answer(self):
        assert util.parse_token(json.dumps(GOOD_ANSWER)) == PARSED

    @pytest.mark.parametrize(
        "body",
        [
            "<html>oops</html>",
            json.dumps(["not", "an", "object"]),
            json.dumps({**GOOD_ANSWER, "token": "ghs_x\nEVIL=1"}),
            json.dumps({**GOOD_ANSWER, "token": "ghs_x y"}),
            json.dumps({**GOOD_ANSWER, "token": ""}),
            json.dumps({**GOOD_ANSWER, "token": 12345}),
            json.dumps({k: v for k, v in GOOD_ANSWER.items() if k != "token"}),
        ],
        ids=["not-json", "not-object", "newline-injection", "embedded-space", "empty-token", "not-a-string", "missing-token"],
    )
    def test_rejects_a_malformed_answer(self, body):
        with pytest.raises(util.MintError):
            util.parse_token(body)

    @pytest.mark.parametrize("grant", [None, 7, "two words"], ids=["absent", "not-a-string", "bad-charset"])
    def test_a_display_only_grant_never_fails_the_mint(self, grant):
        answer = {k: v for k, v in GOOD_ANSWER.items() if k != "grant"}
        if grant is not None:
            answer["grant"] = grant
        assert util.parse_token(json.dumps(answer))["grant"] == "?"

    @pytest.mark.parametrize("installation_id", [None, "1001", True], ids=["absent", "not-an-int", "bool"])
    def test_a_display_only_installation_id_never_fails_the_mint(self, installation_id):
        answer = {k: v for k, v in GOOD_ANSWER.items() if k != "installationId"}
        if installation_id is not None:
            answer["installationId"] = installation_id
        assert util.parse_token(json.dumps(answer))["installationId"] == "?"


class TestOutputBlock:
    def test_exact_lines_and_order(self):
        assert util.output_block(PARSED) == (
            "token=ghs_abc.123-x_y\n"
            "installation-id=1001\n"
            "grant=nolints\n"
            "success=true\n"
        )


class Flaky:
    """A call that raises the scripted errors in turn, then answers."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "body"


class TestWithRetries:
    def test_retries_transport_faults_then_answers(self):
        slept = []
        call = Flaky(urllib.error.URLError("flaky"), http.client.IncompleteRead(b""))
        assert util.with_retries(call, sleep=slept.append) == "body"
        assert call.calls == 3
        assert slept == [1, 2]

    def test_raises_the_last_transport_fault(self):
        call = Flaky(*[TimeoutError()] * util.ATTEMPTS)
        with pytest.raises(TimeoutError):
            util.with_retries(call, sleep=no_sleep)
        assert call.calls == util.ATTEMPTS

    def test_retries_a_5xx_then_answers(self):
        call = Flaky(http_error(503), http_error(502))
        assert util.with_retries(call, sleep=no_sleep) == "body"
        assert call.calls == 3

    def test_retries_a_timeout_or_throttle_then_answers(self):
        call = Flaky(http_error(408), http_error(429))
        assert util.with_retries(call, sleep=no_sleep) == "body"
        assert call.calls == 3

    def test_raises_the_last_5xx(self):
        call = Flaky(*[http_error(503)] * util.ATTEMPTS)
        with pytest.raises(urllib.error.HTTPError) as raised:
            util.with_retries(call, sleep=no_sleep)
        assert raised.value.code == 503
        assert call.calls == util.ATTEMPTS

    def test_any_other_4xx_raises_at_once(self):
        slept = []
        call = Flaky(http_error(403))
        with pytest.raises(urllib.error.HTTPError) as raised:
            util.with_retries(call, sleep=slept.append)
        assert raised.value.code == 403
        assert (call.calls, slept) == (1, [])

    def test_anything_else_raises_at_once(self):
        call = Flaky(RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            util.with_retries(call, sleep=no_sleep)
        assert call.calls == 1


class TestBodyExcerpt:
    def test_a_line_break_cannot_start_a_workflow_command(self):
        body = io.BytesIO(b"denied\n::error::injected\r\n\ttail")
        assert util.body_excerpt(http_error(403, body)) == "denied ::error::injected tail"

    def test_bounds_a_long_body(self):
        body = io.BytesIO(b"<html>" + b"x" * 1000)
        excerpt = util.body_excerpt(http_error(502, body))
        assert len(excerpt) == util.BODY_EXCERPT_CHARS + 3
        assert excerpt.endswith("...")

    def test_an_empty_or_unreadable_body_is_empty(self):
        class Unreadable(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"")

        assert util.body_excerpt(http_error(500)) == ""
        assert util.body_excerpt(http_error(500, Unreadable())) == ""


class TestTransport:
    def test_a_post_sends_the_bearer_the_named_user_agent_and_the_json_body(self, monkeypatch):
        seen = {}

        def urlopen(request, timeout):
            seen["method"] = request.get_method()
            seen["url"] = request.full_url
            seen["agent"] = request.get_header("User-agent")
            seen["auth"] = request.get_header("Authorization")
            seen["content_type"] = request.get_header("Content-type")
            seen["data"] = request.data
            seen["timeout"] = timeout
            return io.BytesIO(b"body")

        monkeypatch.setattr(util.urllib.request, "urlopen", urlopen)
        assert util.fetch_once("https://x.example/p", "b", "POST", '{"app": "x"}') == "body"
        assert seen == {
            "method": "POST",
            "url": "https://x.example/p",
            "agent": util.USER_AGENT,
            "auth": "Bearer b",
            "content_type": "application/json",
            "data": b'{"app": "x"}',
            "timeout": util.TIMEOUT_SECONDS,
        }
        # Cloudflare's browser integrity check rejects Python's default agent.
        assert "urllib" not in util.USER_AGENT

    def test_a_get_sends_no_body_and_no_content_type(self, monkeypatch):
        seen = {}

        def urlopen(request, timeout):
            seen["method"] = request.get_method()
            seen["content_type"] = request.get_header("Content-type")
            seen["data"] = request.data
            return io.BytesIO(b"body")

        monkeypatch.setattr(util.urllib.request, "urlopen", urlopen)
        assert util.fetch_once("https://x.example/p", "b", "GET", None) == "body"
        assert seen == {"method": "GET", "content_type": None, "data": None}

    def test_fetch_text_retries_the_request(self, monkeypatch):
        attempts = []

        def urlopen(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise urllib.error.URLError("flaky")
            return io.BytesIO(b"body")

        monkeypatch.setattr(util.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(util.time, "sleep", no_sleep)
        assert util.fetch_text("https://x.example/p", "b", "GET", None) == "body"
        assert attempts == ["https://x.example/p"] * 2
