#!/bin/bash

# Summarise the messages in a `lake build` log as a Zulip message.
#
# Usage: zulip_build_report.sh <lake build output file>
#
# The Zulip message is written to stdout in the `GITHUB_OUTPUT` format, as the `zulip-message`
# output; if `GITHUB_STEP_SUMMARY` is set, a full report listing every message with all of its
# locations is written there as well.
#
# Environment variables:
# * `TARGET_REPO`/`TARGET_SHA` (default: `REPO`/`SHA`, then `GITHUB_REPOSITORY`/`GITHUB_SHA`):
#   the repository and commit that was built; the location links point here.
# * `WORKFLOW_REPO`/`WORKFLOW_RUN_ID` (default: as above): the workflow run to link to.
# * `WORKFLOW` (default: `GITHUB_WORKFLOW`): the workflow name used in the message header.
# * `SUCCESS`: `true` if the build succeeded.
# * `DESCRIPTION`: an optional line of Markdown, printed after the header, explaining what was run.
# * `INFO`: set to `true` to also report info messages (panics are always reported).
# * `LOCATIONS_MAX_COUNT` (default `0`): in the Zulip message, messages occurring at most this
#   many times list their locations, linked to the source. `0` disables the location column.
# * `MAX_ROWS` (default `0`): the maximum number of rows per table in the Zulip message;
#   `0` means no limit. Zulip messages are limited to 10000 characters.

lean_outfile=$1
target_repo=${TARGET_REPO:-${REPO:-${GITHUB_REPOSITORY}}}
target_sha=${TARGET_SHA:-${SHA:-${GITHUB_SHA}}}
workflow_repo=${WORKFLOW_REPO:-${REPO:-${GITHUB_REPOSITORY}}}
workflow_run_id=${WORKFLOW_RUN_ID:-${RUN_ID:-${GITHUB_RUN_ID}}}
workflow_name=${WORKFLOW:-${GITHUB_WORKFLOW}}
info=${INFO:-false}
description=${DESCRIPTION:-}
locations_max_count=${LOCATIONS_MAX_COUNT:-0}
max_rows=${MAX_ROWS:-0}

run_url="https://github.com/${workflow_repo}/actions/runs/${workflow_run_id}"
blob_url="https://github.com/${target_repo}/blob/${target_sha}/"

# Skip the lines about build progress.
filtered_out=$(grep -v '^✔' "${lean_outfile}" | grep -v '^trace: ')
echo "$(wc -l <<<"${filtered_out}") lines of output" >&2

# Categorize the output.
counts=()
# Panics are reported as an info message.
# They should be very prominent when debugging, so treat them differently.
if panic_lines=$(grep '^info: .*PANIC at ' <<<"${filtered_out}"); then
  counts+=( "$(printf 'Panics: %d' "$(wc -l <<<"${panic_lines}")")" )
  echo "$(wc -l <<<"${panic_lines}") lines of panic" >&2
fi
if error_lines=$(grep '^error: ' <<<"${filtered_out}"); then
  counts+=( "$(printf 'Errors: %d' "$(wc -l <<<"${error_lines}")")" )
  echo "$(wc -l <<<"${error_lines}") lines of errors" >&2
fi
if warning_lines=$(grep '^warning: ' <<<"${filtered_out}"); then
  counts+=( "$(printf 'Warnings: %d' "$(wc -l <<<"${warning_lines}")")" )
  echo "$(wc -l <<<"${warning_lines}") lines of warnings" >&2
fi
if info_lines=$(grep '^info: ' <<<"${filtered_out}" | grep -v 'PANIC at '); then
  counts+=( "$(printf 'Info messages: %d' "$(wc -l <<<"${info_lines}")")" )
  echo "$(wc -l <<<"${info_lines}") lines of info" >&2
fi

# Print a Markdown table of the message lines in $1 (`severity: file:line:col: message`) under
# the column heading $2, one row per distinct message, most frequent first.
# Messages occurring at most $3 times list their locations; $4 caps the rows (0 = no limit).
table() {
  local lines=$1 heading=$2 loc_max=$3 max_rows=$4 rows total
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  rows=$(sed -E 's/^[a-z]*: ([^:]*):([0-9]+):[0-9]+: (.*)$/\3\t\1:\2/' <<<"${lines}" |
    sort -t $'\t' -k1,1 |
    awk -F '\t' -v loc_max="${loc_max}" -v base="${blob_url}" '
      # The input is sorted by message, so equal messages are contiguous.
      function flush() { if (n) printf "%7d\t%s\t%s\n", n, msg, (n <= loc_max ? locs : "") }
      $1 != msg || NR == 1 { flush(); msg = $1; n = 0; locs = "" }
      {
        n++
        if (n <= loc_max && $2 != "") {
          split($2, pos, ":")
          locs = locs (locs == "" ? "" : ", ") "[" $2 "](" base pos[1] "#L" pos[2] ")"
        }
      }
      END { flush() }' |
    sort -t $'\t' -k1,1nr -k2,2r)
  total=$(wc -l <<<"${rows}")
  if (( max_rows > 0 && total > max_rows )); then
    rows=$(head -n "${max_rows}" <<<"${rows}")
    rows+=$'\n\t'"… and $(( total - max_rows )) more distinct messages: see the [full report](${run_url})"
  fi
  if (( loc_max > 0 )); then
    printf '| | %s | Location |\n| ---: | --- | --- |\n' "${heading}"
    awk -F '\t' '{ printf "| %s | %s | %s |\n", $1, $2, $3 }' <<<"${rows}"
  else
    printf '| | %s |\n| ---: | --- |\n' "${heading}"
    awk -F '\t' '{ printf "| %s | %s |\n", $1, $2 }' <<<"${rows}"
  fi
}

if [ "true" == "${SUCCESS}" ]; then
  icon="✅"
  ended="succeeded"
else
  icon="❌"
  ended="failed"
fi

# Write the report, with locations for messages occurring at most $1 times and at most $2 rows
# per table.
report() {
  local loc_max=$1 max_rows=$2
  local header="${icon} ${workflow_name} run on [${target_repo}](https://github.com/${target_repo}) (commit [${target_sha}](https://github.com/${target_repo}/commit/${target_sha}))"
  if (( ${#counts[@]} == 0 )); then
    echo "${header} [${ended} without messages](${run_url})."
    [ -z "${description}" ] || printf '%s\n' "${description}"
  else
    echo "${header} [${ended} with messages](${run_url}):"
    [ -z "${description}" ] || printf '%s\n' "${description}"
    printf '\n* %s' "${counts[@]}"
    printf '\n\n'
  fi

  # $1: message lines, $2: spoiler title, $3: column heading
  section() {
    [ -n "$1" ] || return 0
    echo "\`\`\`spoiler $2"
    table "$1" "$3" "${loc_max}" "${max_rows}"
    echo "\`\`\`"
    echo
  }
  section "${panic_lines}" "Panic counts" "Panic description"
  section "${error_lines}" "Error counts" "Error description"
  section "${warning_lines}" "Warning counts" "Warning description"
  [ "${info}" == "false" ] || section "${info_lines}" "Info message counts" "Info message"
}

delimiter=$(cat /proc/sys/kernel/random/uuid)
echo "zulip-message<<${delimiter}"
report "${locations_max_count}" "${max_rows}"
echo "${delimiter}"

if [ -n "${GITHUB_STEP_SUMMARY}" ]; then
  # The `spoiler` blocks are Zulip syntax; GitHub renders `<details>` instead.
  # shellcheck disable=SC2016 # The backticks are literal Markdown.
  report 1000000000 0 |
    sed -E 's/^```spoiler (.*)$/<details><summary>\1<\/summary>\n/; s/^```$/\n<\/details>/' \
    >> "${GITHUB_STEP_SUMMARY}"
fi
