# mathlib-ci

Trusted CI automation scripts for mathlib4.

This repository hosts scripts that support CI automation and reporting. These scripts are intended for GitHub workflows and bots, not for core Mathlib library development.

mathlib4 workflows should checkout this repository in a trusted path (for
example `ci-tools/`) and execute scripts from `ci-tools/scripts/...`.


## Tests

Ubuntu CI jobs must run `.github/actions/secure-pip` after `actions/setup-python`
and before any package install. The action updates the system CA store, rejects
the compromised roots, and installs pip 26.2.2 from a wheel with a fixed SHA-256.
It also updates certifi to at least 2026.10.19 for clients such as requests.
See the [Python incident notice](https://discuss.python.org/t/109576).

Consumers must update their pinned mathlib-ci commit to receive this fix.
Existing virtual environments and container images need separate updates.

Python test suites live under `tests/`, one directory per script or composite action.
Run them all with `pytest`, or a single suite with `pytest tests/<suite>`. Each suite
also has a `test_*.yml` workflow that runs it whenever its sources or tests change.

Test file basenames must be unique across suites — see the comment in `pytest.ini`.

## Contents
- PR/label/comment automation scripts
- Zulip reporting and emoji sync scripts
- dependency/update monitoring scripts
- nightly automation scripts
- commit verification scripts

