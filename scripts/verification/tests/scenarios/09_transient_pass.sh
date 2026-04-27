#!/usr/bin/env bash
# A pair of transient commits whose net effect is none: the first adds
# `tmp.txt`, the second removes it. The non-transient commits should
# be cherry-pickable cleanly onto the base, and the resulting tree
# should match HEAD's tree.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
make_transient_commit "add scratch file" "tmp.txt=scratch"
make_transient_commit "remove scratch file" "tmp.txt=<DELETE>"
run_and_compare
