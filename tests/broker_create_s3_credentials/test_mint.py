"""The mint policy, driven through an injected transport.

Layout: pure helpers first (word and broker URL checks, the OIDC token
URL, the two answer parsers, the output block), then `mint_credentials()`
for every mint failure, then `run()` for the CLI and the step outputs,
then the retry policy and the one urllib-facing request.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

import mint

BROKER = "https://broker.example.workers.dev/r2-credentials"
AUDIENCE = "example-broker"
OIDC_URL = "https://oidc.example/token?api-version=2"
OIDC_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runtime-token",
    "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
}
OIDC_ANSWER = json.dumps({"value": "jwt"})
GOOD_ANSWER = {
    "accessKeyId": "AKIAMOCK",
    "secretAccessKey": "secret/mock+1=",
    "sessionToken": "sess.token_a-b",
    "grant": "example-grant",
}
TRANSPORT_FAULTS = [urllib.error.URLError("down"), TimeoutError(), http.client.IncompleteRead(b"")]
TRANSPORT_FAULT_IDS = ["url-error", "timeout", "incomplete-read"]


class FakeTransport:
    """A `Fetch` that answers from a script and records every request.

    `oidc` answers the GET to the OIDC token endpoint and `broker`
    answers the POST to the broker. Either may be an exception instance,
    which the fake raises instead.
    """

    def __init__(self, oidc=OIDC_ANSWER, broker=json.dumps(GOOD_ANSWER)):
        self.oidc = oidc
        self.broker = broker
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, url, bearer, method):
        self.calls.append((url, bearer, method))
        answer = self.oidc if method == "GET" else self.broker
        if isinstance(answer, BaseException):
            raise answer
        return answer


def mint_with(transport, env=OIDC_ENV, broker=BROKER, audience=AUDIENCE):
    return mint.mint_credentials(broker, audience, env, transport)


def run_mint(tmp_path, transport=None, env=OIDC_ENV, broker=BROKER, audience=AUDIENCE, github_output=None):
    """Run the CLI; return the exit code, the GITHUB_OUTPUT text, and the log."""
    if github_output is None:
        github_output = tmp_path / "github_output"
        github_output.touch()
    if transport is None:
        transport = FakeTransport()
    out = io.StringIO()
    argv = ["--broker-url", broker, "--audience", audience, "--github-output", str(github_output)]
    code = mint.run(argv, env=env, fetch=transport, out=out)
    outputs = github_output.read_text() if github_output.exists() else ""
    return code, outputs, out.getvalue()


def http_error(code):
    return urllib.error.HTTPError("https://x.example", code, "status", {}, None)


def mask_lines():
    return [f"::add-mask::{GOOD_ANSWER[field]}" for field in mint.CREDENTIAL_FIELDS]


class TestIsPrintableWord:
    @pytest.mark.parametrize("value", ["AKIAMOCK", "secret/mock+1=", "sess.token_a-b", "tökén"])
    def test_accepts_a_printable_token(self, value):
        assert mint.is_printable_word(value)

    @pytest.mark.parametrize(
        "value",
        ["", "two words", "tab\there", "line\nbreak", "cr\rhere", "\x1b[31mred", "sep line", None, 7],
        ids=["empty", "space", "tab", "newline", "cr", "escape", "line-separator", "none", "int"],
    )
    def test_rejects_everything_else(self, value):
        assert not mint.is_printable_word(value)


class TestCheckBrokerUrl:
    @pytest.mark.parametrize("url", ["https://x.example/r2-credentials", "https://x.example:8443/a/b", "https://x.example"])
    def test_accepts_an_https_url_with_a_host(self, url):
        mint.check_broker_url(url)

    @pytest.mark.parametrize(
        "url",
        ["https://x.example\nEVIL=1", "http://x.example", "https://x.example/path ", "https:///path", "https://x.example:99999/p", "x.example/p"],
        ids=["newline", "not-https", "trailing-space", "no-host", "bad-port", "no-scheme"],
    )
    def test_rejects_the_rest(self, url):
        with pytest.raises(mint.MintError, match="broker-url is not one https URL"):
            mint.check_broker_url(url)


class TestOidcTokenUrl:
    def test_appends_to_an_existing_query_string(self):
        assert mint.oidc_token_url("https://x/token?v=2", "aud") == "https://x/token?v=2&audience=aud"

    def test_starts_the_query_string_when_none_exists(self):
        assert mint.oidc_token_url("https://x/token", "aud") == "https://x/token?audience=aud"

    def test_url_encodes_the_audience(self):
        assert mint.oidc_token_url("https://x/token", "a b&c=1/d") == "https://x/token?audience=a+b%26c%3D1%2Fd"


class TestParseOidcToken:
    def test_returns_the_value(self):
        assert mint.parse_oidc_token(OIDC_ANSWER) == "jwt"

    @pytest.mark.parametrize(
        "body",
        ["<html>oops</html>", "{}", "[]", "null", json.dumps({"value": ""}), json.dumps({"value": 5})],
        ids=["not-json", "no-value", "list", "null", "empty-value", "not-a-string"],
    )
    def test_rejects_an_answer_without_a_token(self, body):
        with pytest.raises(mint.MintError, match="the OIDC token response carried no value"):
            mint.parse_oidc_token(body)


class TestParseCredentials:
    def test_accepts_the_broker_answer(self):
        assert mint.parse_credentials(json.dumps(GOOD_ANSWER)) == GOOD_ANSWER

    @pytest.mark.parametrize(
        "body",
        [
            "<html>oops</html>",
            json.dumps(["not", "an", "object"]),
            json.dumps({**GOOD_ANSWER, "accessKeyId": "AKIA\nEVIL=1"}),
            json.dumps({**GOOD_ANSWER, "accessKeyId": "AKIA MOCK"}),
            json.dumps({**GOOD_ANSWER, "secretAccessKey": ""}),
            json.dumps({**GOOD_ANSWER, "sessionToken": 12345}),
            json.dumps({k: v for k, v in GOOD_ANSWER.items() if k != "sessionToken"}),
        ],
        ids=["not-json", "not-object", "newline-injection", "embedded-space", "empty-field", "not-a-string", "missing-session-token"],
    )
    def test_rejects_a_malformed_answer(self, body):
        with pytest.raises(mint.MintError):
            mint.parse_credentials(body)

    @pytest.mark.parametrize("grant", [None, 7, "two words"], ids=["absent", "not-a-string", "bad-charset"])
    def test_a_display_only_grant_never_fails_the_mint(self, grant):
        answer = {k: v for k, v in GOOD_ANSWER.items() if k != "grant"}
        if grant is not None:
            answer["grant"] = grant
        assert mint.parse_credentials(json.dumps(answer))["grant"] == "?"


class TestOutputBlock:
    def test_exact_lines_and_order(self):
        assert mint.output_block(GOOD_ANSWER) == (
            "access-key-id=AKIAMOCK\n"
            "secret-access-key=secret/mock+1=\n"
            "session-token=sess.token_a-b\n"
            "grant=example-grant\n"
            "minted=true\n"
        )


class TestMintCredentials:
    def test_returns_the_validated_credentials(self):
        transport = FakeTransport()
        assert mint_with(transport) == GOOD_ANSWER
        assert transport.calls == [
            (f"{OIDC_URL}&audience={AUDIENCE}", "runtime-token", "GET"),
            (BROKER, "jwt", "POST"),
        ]

    def test_url_encodes_the_audience_in_the_token_request(self):
        transport = FakeTransport()
        mint_with(transport, audience="other.broker/aud")
        assert transport.calls[0][0] == f"{OIDC_URL}&audience=other.broker%2Faud"

    @pytest.mark.parametrize("url", ["https://x.example\nEVIL=1", "http://x.example"], ids=["newline", "not-https"])
    def test_a_rejected_broker_url_never_reaches_the_transport(self, url):
        transport = FakeTransport()
        with pytest.raises(mint.MintError, match="broker-url is not one https URL"):
            mint_with(transport, broker=url)
        assert transport.calls == []

    def test_an_empty_audience_never_reaches_the_transport(self):
        transport = FakeTransport()
        with pytest.raises(mint.MintError, match="audience is empty"):
            mint_with(transport, audience="")
        assert transport.calls == []

    def test_no_oidc_endpoint_never_reaches_the_transport(self):
        transport = FakeTransport()
        with pytest.raises(mint.MintError, match=r"no OIDC token endpoint \(id-token: write missing\?\)"):
            mint_with(transport, env={})
        assert transport.calls == []

    def test_an_oidc_endpoint_error_status_names_the_status(self):
        with pytest.raises(mint.MintError, match="the GitHub OIDC token endpoint answered HTTP 500"):
            mint_with(FakeTransport(oidc=http_error(500)))

    @pytest.mark.parametrize("fault", TRANSPORT_FAULTS, ids=TRANSPORT_FAULT_IDS)
    def test_an_oidc_transport_fault_is_a_mint_failure(self, fault):
        with pytest.raises(mint.MintError, match="could not obtain the GitHub OIDC token"):
            mint_with(FakeTransport(oidc=fault))

    def test_an_oidc_answer_without_a_value_never_reaches_the_broker(self):
        transport = FakeTransport(oidc="{}")
        with pytest.raises(mint.MintError, match="the OIDC token response carried no value"):
            mint_with(transport)
        assert [method for _, _, method in transport.calls] == ["GET"]

    def test_a_broker_error_status_names_the_status(self):
        with pytest.raises(mint.MintError, match="the broker answered HTTP 403"):
            mint_with(FakeTransport(broker=http_error(403)))

    @pytest.mark.parametrize("fault", TRANSPORT_FAULTS, ids=TRANSPORT_FAULT_IDS)
    def test_a_broker_transport_fault_is_a_mint_failure(self, fault):
        with pytest.raises(mint.MintError, match="the broker did not answer with credentials"):
            mint_with(FakeTransport(broker=fault))

    def test_a_200_with_a_malformed_body_is_a_mint_failure(self):
        with pytest.raises(mint.MintError, match="the broker answer was not JSON"):
            mint_with(FakeTransport(broker="<html>oops</html>"))

    def test_a_bug_in_the_transport_is_not_a_mint_failure(self):
        # Only transport faults become a MintError. Anything else is a
        # programming error and must surface as a traceback.
        with pytest.raises(RuntimeError, match="bug"):
            mint_with(FakeTransport(broker=RuntimeError("bug")))


class TestRun:
    def test_happy_path_sets_outputs_and_masks(self, tmp_path):
        code, outputs, log = run_mint(tmp_path)
        assert code == 0
        assert outputs == mint.output_block(GOOD_ANSWER)
        # Every credential is masked before the summary line prints.
        assert log.splitlines() == [*mask_lines(), "credentials minted (grant: example-grant)"]

    def test_the_cli_inputs_reach_the_mint(self, tmp_path):
        transport = FakeTransport()
        code, _, _ = run_mint(tmp_path, transport, broker="https://other.example/creds", audience="other-aud")
        assert code == 0
        assert transport.calls == [
            (f"{OIDC_URL}&audience=other-aud", "runtime-token", "GET"),
            ("https://other.example/creds", "jwt", "POST"),
        ]

    def test_a_mint_failure_fails_the_step_with_no_outputs(self, tmp_path):
        code, outputs, log = run_mint(tmp_path, FakeTransport(broker=http_error(403)))
        assert (code, outputs) == (1, "")
        assert log == "::error::the broker answered HTTP 403\n"

    def test_an_unwritable_github_output_fails_after_masking(self, tmp_path):
        missing = tmp_path / "no-such-dir" / "github_output"
        code, outputs, log = run_mint(tmp_path, github_output=missing)
        assert (code, outputs) == (1, "")
        lines = log.splitlines()
        # The credentials were masked before the failed write.
        assert lines[:-1] == mask_lines()
        assert lines[-1].startswith("::error::could not write the step outputs")


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


def no_sleep(_seconds):
    pass


class TestWithRetries:
    def test_retries_transport_faults_then_answers(self):
        slept = []
        call = Flaky(urllib.error.URLError("flaky"), http.client.IncompleteRead(b""))
        assert mint.with_retries(call, sleep=slept.append) == "body"
        assert call.calls == 3
        assert slept == [1, 2]

    def test_raises_the_last_transport_fault(self):
        call = Flaky(*[TimeoutError()] * mint.ATTEMPTS)
        with pytest.raises(TimeoutError):
            mint.with_retries(call, sleep=no_sleep)
        assert call.calls == mint.ATTEMPTS

    def test_retries_a_5xx_then_answers(self):
        call = Flaky(http_error(503), http_error(502))
        assert mint.with_retries(call, sleep=no_sleep) == "body"
        assert call.calls == 3

    def test_raises_the_last_5xx(self):
        call = Flaky(*[http_error(503)] * mint.ATTEMPTS)
        with pytest.raises(urllib.error.HTTPError) as raised:
            mint.with_retries(call, sleep=no_sleep)
        assert raised.value.code == 503
        assert call.calls == mint.ATTEMPTS

    def test_a_4xx_raises_at_once(self):
        slept = []
        call = Flaky(http_error(403))
        with pytest.raises(urllib.error.HTTPError) as raised:
            mint.with_retries(call, sleep=slept.append)
        assert raised.value.code == 403
        assert (call.calls, slept) == (1, [])

    def test_anything_else_raises_at_once(self):
        call = Flaky(RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            mint.with_retries(call, sleep=no_sleep)
        assert call.calls == 1


class TestTransport:
    def test_sends_the_bearer_and_the_named_user_agent(self, monkeypatch):
        seen = {}

        def urlopen(request, timeout):
            seen["method"] = request.get_method()
            seen["url"] = request.full_url
            seen["agent"] = request.get_header("User-agent")
            seen["auth"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return io.BytesIO(b"body")

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        assert mint.fetch_once("https://x.example/p", "b", "POST") == "body"
        assert seen == {
            "method": "POST",
            "url": "https://x.example/p",
            "agent": mint.USER_AGENT,
            "auth": "Bearer b",
            "timeout": mint.TIMEOUT_SECONDS,
        }
        # Cloudflare's browser integrity check rejects Python's default agent.
        assert "urllib" not in mint.USER_AGENT

    def test_fetch_text_retries_the_request(self, monkeypatch):
        attempts = []

        def urlopen(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise urllib.error.URLError("flaky")
            return io.BytesIO(b"body")

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(mint.time, "sleep", no_sleep)
        assert mint.fetch_text("https://x.example/p", "b", "GET") == "body"
        assert attempts == ["https://x.example/p"] * 2
