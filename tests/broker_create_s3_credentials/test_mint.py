"""The mint flow and the CLI in `mint.py`, driven through a recording
fake transport.

Layout: `mint_credentials()` for every mint failure, then `run()` for
the CLI and the step outputs.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

import mint
import util

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
    which the fake raises.
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
    return [f"::add-mask::{GOOD_ANSWER[field]}" for field in util.CREDENTIAL_FIELDS]


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
        with pytest.raises(util.MintError, match="broker-url is not one https URL"):
            mint_with(transport, broker=url)
        assert transport.calls == []

    def test_an_empty_audience_never_reaches_the_transport(self):
        transport = FakeTransport()
        with pytest.raises(util.MintError, match="audience is empty"):
            mint_with(transport, audience="")
        assert transport.calls == []

    def test_no_oidc_endpoint_never_reaches_the_transport(self):
        transport = FakeTransport()
        with pytest.raises(util.MintError, match=r"no OIDC token endpoint \(id-token: write missing\?\)"):
            mint_with(transport, env={})
        assert transport.calls == []

    def test_an_oidc_endpoint_error_status_names_the_status(self):
        with pytest.raises(util.MintError, match="the GitHub OIDC token endpoint answered HTTP 500"):
            mint_with(FakeTransport(oidc=http_error(500)))

    @pytest.mark.parametrize("fault", TRANSPORT_FAULTS, ids=TRANSPORT_FAULT_IDS)
    def test_an_oidc_transport_fault_is_a_mint_failure(self, fault):
        with pytest.raises(util.MintError, match="could not obtain the GitHub OIDC token"):
            mint_with(FakeTransport(oidc=fault))

    def test_an_oidc_answer_without_a_value_never_reaches_the_broker(self):
        transport = FakeTransport(oidc="{}")
        with pytest.raises(util.MintError, match="the OIDC token response carried no value"):
            mint_with(transport)
        assert [method for _, _, method in transport.calls] == ["GET"]

    def test_a_broker_error_status_names_the_status(self):
        with pytest.raises(util.MintError, match="the broker answered HTTP 403"):
            mint_with(FakeTransport(broker=http_error(403)))

    @pytest.mark.parametrize("fault", TRANSPORT_FAULTS, ids=TRANSPORT_FAULT_IDS)
    def test_a_broker_transport_fault_is_a_mint_failure(self, fault):
        with pytest.raises(util.MintError, match="the broker did not answer with credentials"):
            mint_with(FakeTransport(broker=fault))

    def test_a_200_with_a_malformed_body_is_a_mint_failure(self):
        with pytest.raises(util.MintError, match="the broker answer was not JSON"):
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
        assert outputs == util.output_block(GOOD_ANSWER)
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
