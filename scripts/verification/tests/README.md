# Verification script tests

End-to-end tests for `scripts/verification/verify_commits.sh` and
`verify_commits_summary.sh`. Each scenario builds a small synthetic git
history, runs the verification scripts against it, and compares the
captured JSON, rendered comment, and exit code against golden files.

## Running

From this directory:

```sh
./run.sh                    # run all scenarios
./run.sh 03 cascade         # run only scenarios whose name contains "03" or "cascade"
UPDATE_GOLDEN=1 ./run.sh    # update goldens to match current output
```

The driver returns 0 if all scenarios pass, 1 otherwise.

## Layout

```
tests/
├── README.md
├── run.sh         # driver
├── lib.sh         # shared helpers (setup_repo, make_*_commit, run_and_compare, ...)
├── scenarios/     # one scenario per file; sorted lexically
└── golden/        # one .json + .md + .exit per scenario
```

## How a scenario works

Each scenario file is a short bash script that sources `lib.sh` and:

1. Calls `setup_repo` to create a fresh tmp git repo with a deterministic
   initial commit on `master`, then switches to a `feature` branch.
2. Calls a sequence of `make_commit` / `make_auto_commit` /
   `make_transient_commit` calls to build the commit history.
3. Calls `run_and_compare`, which runs `verify_commits.sh`, runs
   `verify_commits_summary.sh`, normalizes SHAs, and diffs the result
   against `golden/<scenario>.{json,md,exit}`.

`make_auto_commit <command> [<file>=<content>...]` commits the listed file
contents under the subject `x: <command>`. The contents committed are
exactly what's specified — independent of what `<command>` would actually
produce when run. This lets scenarios craft "tree mismatch" cases (where
the committed tree disagrees with the command's output) and "missing
command" / "parse error" cases (where the command can't even run).

## Determinism

Commits use fixed author/committer identity and per-commit incrementing
timestamps. SHAs are reproducible across runs on the same git version,
and the comparison normalizes 40-char SHAs to `<SHA>` and 7-char short
SHAs in code spans to `<SHORT>`. So goldens are robust to git version
differences.

The timeout test scenario sets `SCENARIO_TIMEOUT=1` to keep the test
fast (the script's default is 600s).

## Updating goldens

When the script's output legitimately changes, update goldens with:

```sh
UPDATE_GOLDEN=1 ./run.sh
```

Then review the diff in `golden/` before committing.

## Caveats

- These tests produce output that depends on the host system's `bash`
  error messages, `xargs` flavor (BSD vs GNU), etc. The goldens were
  generated on macOS bash 3.2 / BSD coreutils. If you run on Linux you
  may see legitimate output differences (e.g., different "command not
  found" wording); regenerate with `UPDATE_GOLDEN=1` and review.
- There is no CI yet for this repo. These tests are run by hand. Future
  work: wire them up to a GitHub Actions job.
