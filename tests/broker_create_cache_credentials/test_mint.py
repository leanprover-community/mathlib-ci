"""The mint policy, driven through an injected transport.

Layout: pure helpers first (audience URL, credential parsing, the
export block), then `run()` end to end for both failure postures, then
the transport's retry behavior.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import mint

BROKER = "https://broker.example.workers.dev"
PUT_BASE = "https://acct.r2.cloudflarestorage.com/mathlib4-devcache"
OIDC_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runtime-token",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?api-version=2",
}
GOOD_ANSWER = {
    "accessKeyId": "AKIAMOCK",
    "secretAccessKey": "secret/mock+1=",
    "sessionToken": "sess.token_a-b",
    "grant": "cache-upload-forks",
}


def good_fetch(url, bearer, method="GET"):
    if method == "GET":
        assert "audience=mathlib-cache-broker" in url
        assert bearer == "runtime-token"
        return json.dumps({"value": "jwt"})
    assert url == f"{BROKER}/r2-credentials"
    assert bearer == "jwt"
    return json.dumps(GOOD_ANSWER)


def args(on_failure, github_env):
    return [
        "--broker-url", BROKER,
        "--put-base-url", PUT_BASE,
        "--audience", "mathlib-cache-broker",
        "--on-failure", on_failure,
        "--github-env", str(github_env),
    ]


def run_mint(tmp_path, on_failure="warn-and-skip", fetch=good_fetch, env=OIDC_ENV, broker=None):
    github_env = tmp_path / "github_env"
    github_env.touch()
    out = io.StringIO()
    argv = args(on_failure, github_env)
    if broker is not None:
        argv[1] = broker
    code = mint.run(argv, env=env, fetch=fetch, out=out)
    return code, github_env.read_text(), out.getvalue()


class TestAudienceUrl:
    def test_appends_to_an_existing_query_string(self):
        assert mint.audience_url("https://x/token?v=2", "aud") == "https://x/token?v=2&audience=aud"

    def test_starts_the_query_string_when_none_exists(self):
        assert mint.audience_url("https://x/token", "aud") == "https://x/token?audience=aud"


class TestParseCredentials:
    def test_accepts_the_broker_answer(self):
        credentials = mint.parse_credentials(json.dumps(GOOD_ANSWER))
        assert credentials["accessKeyId"] == "AKIAMOCK"
        assert credentials["grant"] == "cache-upload-forks"

    @pytest.mark.parametrize(
        "body",
        [
            "<html>oops</html>",
            json.dumps(["not", "an", "object"]),
            json.dumps({**GOOD_ANSWER, "accessKeyId": "AKIA\nEVIL=1"}),
            json.dumps({**GOOD_ANSWER, "secretAccessKey": ""}),
            json.dumps({k: v for k, v in GOOD_ANSWER.items() if k != "sessionToken"}),
        ],
        ids=["not-json", "not-object", "newline-injection", "empty-field", "missing-session-token"],
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


class TestExportBlock:
    def test_exact_lines_and_order(self):
        credentials = mint.parse_credentials(json.dumps(GOOD_ANSWER))
        assert mint.export_block(credentials, PUT_BASE) == (
            "MATHLIB_CACHE_S3_ACCESS_KEY_ID=AKIAMOCK\n"
            "MATHLIB_CACHE_S3_SECRET_ACCESS_KEY=secret/mock+1=\n"
            "MATHLIB_CACHE_S3_SESSION_TOKEN=sess.token_a-b\n"
            f"MATHLIB_CACHE_PUT_BASE_URL={PUT_BASE}\n"
            "MATHLIB_CACHE_DEVELOPER_MINTED=true\n"
        )


class TestRun:
    def test_happy_path_exports_and_masks(self, tmp_path):
        code, exported, output = run_mint(tmp_path)
        assert code == 0
        assert "MATHLIB_CACHE_DEVELOPER_MINTED=true\n" in exported
        assert exported.endswith("MINTED=true\n")
        # Every credential is masked before the summary line prints.
        mask_lines = [line for line in output.splitlines() if line.startswith("::add-mask::")]
        assert len(mask_lines) == 3
        assert output.splitlines()[-1] == "cache credentials minted (grant: cache-upload-forks)"
        assert output.index("::add-mask::") < output.index("minted")

    def test_no_oidc_endpoint_warns_and_skips(self, tmp_path):
        code, exported, output = run_mint(tmp_path, env={})
        assert code == 0
        assert exported == ""
        assert output.startswith("::warning::the job has no OIDC token endpoint")
        assert "will be skipped" in output

    def test_no_oidc_endpoint_fails_in_fail_posture(self, tmp_path):
        code, exported, output = run_mint(tmp_path, on_failure="fail", env={})
        assert code == 1
        assert exported == ""
        assert output.startswith("::error::the job has no OIDC token endpoint")

    def test_broker_transport_error_takes_the_failure_path(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            if method == "POST":
                raise urllib.error.URLError("boom")
            return json.dumps({"value": "jwt"})

        code, exported, output = run_mint(tmp_path, fetch=fetch)
        assert (code, exported) == (0, "")
        assert "the cache broker did not answer with credentials" in output
        code, exported, _ = run_mint(tmp_path, on_failure="fail", fetch=fetch)
        assert (code, exported) == (1, "")

    def test_a_200_with_a_malformed_body_takes_the_failure_path(self, tmp_path):
        def fetch(url, bearer, method="GET"):
            return json.dumps({"value": "jwt"}) if method == "GET" else "<html>oops</html>"

        code, exported, output = run_mint(tmp_path, fetch=fetch)
        assert (code, exported) == (0, "")
        assert "the broker answer was not JSON" in output

    def test_an_oidc_answer_without_a_value_takes_the_failure_path(self, tmp_path):
        code, exported, output = run_mint(tmp_path, fetch=lambda *a, **k: "{}")
        assert (code, exported) == (0, "")
        assert "the OIDC token response carried no value" in output

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.example\nEVIL=1",
            "http://x.example",
            "https://x.example/path?query=1",
            "https://x.example/path ",
        ],
        ids=["newline-injection", "not-https", "query-string", "trailing-space"],
    )
    def test_a_url_input_outside_the_charset_never_reaches_github_env(self, tmp_path, url):
        code, exported, output = run_mint(tmp_path, broker=url)
        assert (code, exported) == (0, "")
        assert "broker-url is not one plain https URL" in output
        github_env = tmp_path / "github_env"
        github_env.write_text("")
        out = io.StringIO()
        argv = args("warn-and-skip", github_env)
        argv[3] = url  # --put-base-url value
        assert mint.run(argv, env=OIDC_ENV, fetch=good_fetch, out=out) == 0
        assert github_env.read_text() == ""
        assert "put-base-url is not one plain https URL" in out.getvalue()


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
