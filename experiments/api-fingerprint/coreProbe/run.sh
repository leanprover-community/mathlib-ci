#!/usr/bin/env bash
# PROBE: #99's dumper, then the core and the project script at once, on the same roots, then
# compare.py. Run from a mathlib checkout with the roots' oleans:
#
#   run.sh DUMPER OUT_DIR ROOT [ROOT ...]
#
# DUMPER is #99's dumpReasonableDecls.lean (`6d4d41a`). Writes OUT_DIR/old and OUT_DIR/new, and
# each process's time and peak memory to OUT_DIR/*.time. Times on a laptop: ratios only.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dumper=$1 out=$2
shift 2
mkdir -p "$out/old" "$out/new"
lake --dir "$here" build >/dev/null
# `lean` directly, not through `lake env`, which is noisier for timing (DEVELOPING.md, *Gotchas*).
lean_path="$(lake env printenv LEAN_PATH)"
lean="$(lake env printenv LEAN_SYSROOT)/bin/lean"
export LEAN_SRC_PATH="$(lake env printenv LEAN_SRC_PATH)"
timed() { local name=$1; shift; /usr/bin/time -l "$@" 2> >(tee "$out/$name.time" >&2); }

echo "== #99's dumper"
LEAN_PATH="$lean_path" timed old "$lean" --run "$dumper" --out-dir "$out/old" "$@"
echo "== the core and the project script, at once"
LEAN_PATH="$lean_path" timed core "$here/.lake/build/bin/coreprobe" --out "$out/new/api.jsonl" "$@" &
core=$!
LEAN_PATH="$here/.lake/build/lib/lean:$lean_path" timed project \
  "$lean" --run "$here/ProjectProbe.lean" --out-dir "$out/new" "$@" &
project=$!
wait $core
wait $project
for f in old core project; do
  printf '%-8s %6s s real, %5s MB peak\n' "$f" \
    "$(awk '/ real /{print $1}' "$out/$f.time")" \
    "$(awk '/maximum resident set size/{printf "%d", $1/1048576}' "$out/$f.time")"
done
python3 "$here/compare.py" "$out/old" "$out/new"
