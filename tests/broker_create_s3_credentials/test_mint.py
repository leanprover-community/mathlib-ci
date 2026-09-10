"""The mint policy, driven through an injected transport.

Layout: pure helpers first (audience URL, credential parsing, the
output block), then `run()` end to end for both failure postures, then
the transport's retry behavior.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import mint

BROKER = "https://broker.example.workers.dev/r2-credentials"
AUDIENCE = "example-broker"
OIDC_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runtime-token",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?api-version=2",
}
GOOD_ANSWER = {
    "accessKeyId": "AKIAMOCK",
    "secretAccessKey": "secret/mock+1=",
    "sessionToken": "sess.token_a-b",
    "grant": "example-grant",
}


def good_fetch(url, bearer, method="GET"):
    if method == "GET":
        assert url.endswith(f"&audience={AUDIENCE}")
        assert bearer == "runtime-token"
        return json.dumps({"value": "jwt"})
    assert url == BROKER
    assert bearer == "jwt"
    return json.dumps(GOOD_ANSWER)


def args(on_failure, github_output, broker=BROKER, audience=AUDIENCE):
    return [
        "--broker-url", broker,
        "--audience", audience,
        "--on-failure", on_failure,
        "--github-output", str(github_output),
    ]


def run_mint(
    tmp_path,
    on_failure="warn-and-skip",
    fetch=good_fetch,
    env=OIDC_ENV,
    broker=BROKER,
    audience=AUDIENCE,
    github_output=None,
):
    """Run the mint; return the exit code, the GITHUB_OUTPUT text, and the log."""
    if github_output is None:
        github_output = tmp_path / "github_output"
        github_output.touch()
    out = io.StringIO()
    code = mint.run(args(on_failure, github_output, broker, audience), env=env, fetch=fetch, out=out)
    outputs = github_output.read_text() if github_output.exists() else ""
    return code, outputs, out.getvalue()


def http_error(code):
    return urllib.error.HTTPError("https://x.example", code, "status", {}, None)


class TestIsToken:
    @pytest.mark.parametrize("value", ["AKIAMOCK", "secret/mock+1=", "sess.token_a-b", "tökén"])
    def test_accepts_a_printable_token(self, value):
        assert mint.is_token(value)

    @pytest.mark.parametrize(
        "value",
        ["", "two words", "tab\there", "line\nbreak", "cr\rhere", "\x1b[31mred", "sep\u2028line", None, 7],
        ids=["empty", "space", "tab", "newline", "cr", "escape", "line-separator", "none", "int"],
    )
    def test_rejects_everything_else(self, value):
        assert not mint.is_token(value)


class TestCheckEndpoint:
    @pytest.mark.parametrize("url", ["https://x.example/r2-credentials", "https://x.example:8443/a/b", "https://x.example"])
    def test_accepts_an_https_url_with_a_host(self, url):
        mint.check_endpoint(url)

    @pytest.mark.parametrize(
        "url",
        ["https://x.example\nEVIL=1", "http://x.example", "https://x.example/path ", "https:///path", "https://x.example:99999/p", "x.example/p"],
        ids=["newline", "not-https", "trailing-space", "no-host", "bad-port", "no-scheme"],
    )
    def test_rejects_the_rest(self, url):
        with pytest.raises(mint.MintError, match="broker-url is not one https URL"):
            mint.check_endpoint(url)


class TestAudienceUrl:
    def test_appends_to_an_existing_query_string(self):
        assert mint.audience_url("https://x/token?v=2", "aud") == "https://x/token?v=2&audience=aud"

    def test_starts_the_query_string_when_none_exists(self):
        assert mint.audience_url("https://x/token", "aud") == "https://x/token?audience=aud"

    def test_url_encodes_the_audience(self):
        assert mint.audience_url("https://x/token", "a b&c=1/d") == "https://x/token?audience=a+b%26c%3D1%2Fd"


class TestParseCredentials:
    def test_accepts_the_broker_answer(self):
        credentials = mint.parse_credentials(json.dumps(GOOD_ANSWER))
        assert credentials["accessKeyId"] == "AKIAMOCK"
        assert credentials["grant"] == "example-grant"

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
        credentials = mint.parse_credentials(json.dumps(GOOD_ANSWER))
        assert mint.output_block(credentials) == (
            "access-key-id=AKIAMOCK\n"
            "secret-access-key=secret/mock+1=\n"
            "session-token=sess.token_a-b\n"
            "grant=example-grant\n"
            "minted=true\n"
        )


class TestRun:
    def test_happy_path_sets_outputs_and_masks(self, tmp_path):
        code, outputs, output = run_mint(tmp_path)
        assert code == 0
        assert "session-token=sess.token_a-b\n" in outputs
        assert outputs.endswith("minted=true\n")
        # Every credential is masked before the summary line prints.
        mask_lines = [line for line in output.splitlines() if line.startswith("::add-mask::")]
        assert len(mask_lines) == 3
        assert output.splitlines()[-1] == "credentials minted (grant: example-grant)"
        assert output.index("::add-mask::") < output.index("minted")

    def test_no_oidc_endpoint_warns_and_skips(self, tmp_path):
        code, outputs, output = run_mint(tmp_path, env={})
        assert code == 0
        assert outputs == ""
        assert output.startswith("::warning::the job has no OIDC token endpoint")
        assert output.endswith("(id-token: write missing?). The step set no outputs.\n")

    def test_no_oidc_endpoint_fails_in_fail_posture(self, tmp_path):
        code, outputs, output = run_mint(tmp_path, on_failure="fail", env={})
        assert code == 1
        assert outputs == ""
        assert output.startswith("::error::the job has no OIDC token endpoint")

    def test_broker_transport_error_takes_the_failure_path(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            if method == "POST":
                raise urllib.error.URLError("boom")
            return json.dumps({"value": "jwt"})

        code, outputs, output = run_mint(tmp_path, fetch=fetch)
        assert (code, outputs) == (0, "")
        assert "the broker did not answer with credentials" in output
        code, outputs, _ = run_mint(tmp_path, on_failure="fail", fetch=fetch)
        assert (code, outputs) == (1, "")

    def test_a_broker_error_status_names_the_status(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            if method == "POST":
                raise http_error(403)
            return json.dumps({"value": "jwt"})

        code, outputs, output = run_mint(tmp_path, fetch=fetch)
        assert (code, outputs) == (0, "")
        assert "the broker answered HTTP 403" in output
        code, outputs, output = run_mint(tmp_path, on_failure="fail", fetch=fetch)
        assert (code, outputs) == (1, "")
        assert output.startswith("::error::the broker answered HTTP 403")

    def test_an_oidc_endpoint_error_status_names_the_status(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            raise http_error(500)

        code, outputs, output = run_mint(tmp_path, fetch=fetch)
        assert (code, outputs) == (0, "")
        assert "the GitHub OIDC token endpoint answered HTTP 500" in output

    def test_a_200_with_a_malformed_body_takes_the_failure_path(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            return json.dumps({"value": "jwt"}) if method == "GET" else "<html>oops</html>"

        code, outputs, output = run_mint(tmp_path, fetch=fetch)
        assert (code, outputs) == (0, "")
        assert "the broker answer was not JSON" in output

    def test_an_oidc_answer_without_a_value_takes_the_failure_path(self, tmp_path):
        code, outputs, output = run_mint(tmp_path, fetch=lambda *a, **k: "{}")
        assert (code, outputs) == (0, "")
        assert "the OIDC token response carried no value" in output

    def test_the_audience_input_reaches_the_token_request(self, tmp_path):
        seen = []

        def fetch(url, bearer, method="GET"):
            seen.append(url)
            return json.dumps({"value": "jwt"}) if method == "GET" else json.dumps(GOOD_ANSWER)

        code, outputs, _ = run_mint(tmp_path, fetch=fetch, audience="other.broker/aud")
        assert code == 0 and outputs.endswith("minted=true\n")
        assert seen == [f"{OIDC_ENV['ACTIONS_ID_TOKEN_REQUEST_URL']}&audience=other.broker%2Faud", BROKER]

    def test_an_empty_audience_takes_the_failure_path(self, tmp_path):
        def fetch(*a, **k):
            raise AssertionError("the transport must not run without an audience")

        code, outputs, output = run_mint(tmp_path, fetch=fetch, audience="")
        assert (code, outputs) == (0, "")
        assert "audience is empty" in output

    def test_an_unwritable_github_output_follows_the_posture(self, tmp_path):
        missing = tmp_path / "no-such-dir" / "github_output"
        code, outputs, output = run_mint(tmp_path, github_output=missing)
        assert (code, outputs) == (0, "")
        assert "::warning::could not write the step outputs" in output
        # The credentials were still masked before the failed write.
        assert output.count("::add-mask::") == 3
        code, outputs, output = run_mint(tmp_path, on_failure="fail", github_output=missing)
        assert (code, outputs) == (1, "")
        assert "::error::could not write the step outputs" in output

    @pytest.mark.parametrize("url", ["https://x.example\nEVIL=1", "http://x.example"], ids=["newline", "not-https"])
    def test_a_rejected_broker_url_never_reaches_the_transport(self, tmp_path, url):
        def fetch(*a, **k):
            raise AssertionError("the transport must not see a rejected URL")

        code, outputs, output = run_mint(tmp_path, broker=url, fetch=fetch)
        assert (code, outputs) == (0, "")
        assert "broker-url is not one https URL" in output


class TestFetchRetries:
    def test_retries_transport_failures_then_succeeds(self, monkeypatch):
        calls = []
        slept = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"body"

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                raise urllib.error.URLError("flaky")
            return Response()

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        assert mint.fetch_text("https://x.example", "b", sleep=slept.append) == "body"
        assert len(calls) == 3
        assert slept == [1, 2]

    def test_raises_after_the_last_attempt(self, monkeypatch):
        def urlopen(request, timeout):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        with pytest.raises(urllib.error.URLError):
            mint.fetch_text("https://x.example", "b", sleep=lambda _: None)

    def test_retries_a_5xx_but_not_a_4xx(self, monkeypatch):
        calls = []

        def urlopen(request, timeout):
            calls.append(request.get_method())
            raise http_error(503 if len(calls) < 3 else 403)

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        with pytest.raises(urllib.error.HTTPError) as raised:
            mint.fetch_text("https://x.example", "b", method="POST", sleep=lambda _: None)
        assert raised.value.code == 403
        assert calls == ["POST", "POST", "POST"]

    def test_sends_the_named_user_agent(self, monkeypatch):
        seen = {}

        def urlopen(request, timeout):
            seen["agent"] = request.get_header("User-agent")
            seen["auth"] = request.get_header("Authorization")
            raise http_error(401)

        monkeypatch.setattr(mint.urllib.request, "urlopen", urlopen)
        with pytest.raises(urllib.error.HTTPError):
            mint.fetch_text("https://x.example", "b", sleep=lambda _: None)
        assert seen == {"agent": mint.USER_AGENT, "auth": "Bearer b"}
        assert "urllib" not in mint.USER_AGENT
