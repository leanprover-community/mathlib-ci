#!/bin/bash
shopt -s extglob

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

# Tabulate message lines of the form `severity: file:line:col: message`, one row per distinct
# message, most frequent first, as `count<TAB>message<TAB>locations`.
# Rows for messages occurring at most $2 times (default: all) list their locations as links.
tabulate() {
  local lines=$1 loc_max=${2:-1000000000}
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  sed -E 's/^[a-z]*: //; s/^([^:]*):([0-9]+):[0-9]+: (.*)$/\3\t\1:\2/' <<<"${lines}" |
    sort -t $'\t' -k1,1 |
    awk -F '\t' -v loc_max="${loc_max}" -v base="${blob_url}" '
      # The input is sorted by message, so equal messages are contiguous.
      function flush() { if (n) printf "%d\t%s\t%s\n", n, msg, (n <= loc_max ? locs : "") }
      $1 != msg || NR == 1 { flush(); msg = $1; n = 0; locs = "" }
      {
        n++
        if (n <= loc_max && $2 != "") {
          split($2, pos, ":")
          locs = locs (locs == "" ? "" : ", ") "[" $2 "](" base pos[1] "#L" pos[2] ")"
        }
      }
      END { flush() }' |
    sort -t $'\t' -k1,1nr -k2,2
}

# Print a Markdown table for the tabulated rows in $1, with column heading $2.
# If $3 is `true` a locations column is included; $4 (default: 0 = unlimited) caps the rows.
table() {
  local rows=$1 heading=$2 with_locations=$3 max=${4:-0}
  local total
  total=$(wc -l <<<"${rows}")
  if [ "${with_locations}" == "true" ]; then
    echo "| | ${heading} | Location |"
    echo "| ---: | --- | --- |"
    awk -F '\t' '{ printf "| %s | %s | %s |\n", $1, $2, $3 }' <<<"${rows}" |
      { if (( max > 0 )); then head -n "${max}"; else cat; fi; }
  else
    echo "| | ${heading} |"
    echo "| ---: | --- |"
    awk -F '\t' '{ printf "| %s | %s |\n", $1, $2 }' <<<"${rows}" |
      { if (( max > 0 )); then head -n "${max}"; else cat; fi; }
  fi
  if (( max > 0 && total > max )); then
    echo "| | … and $(( total - max )) more distinct messages: see the [full report](${run_url}) | |"
  fi
}

if [ "true" == "${SUCCESS}" ]; then
  icon="✅"
  ended="succeeded"
else
  icon="❌"
  ended="failed"
fi

header="${icon} ${workflow_name} run on [${target_repo}](https://github.com/${target_repo}) (commit [${target_sha}](https://github.com/${target_repo}/commit/${target_sha}))"

# Write the Zulip message ($1 = "zulip") or the full report ($1 = "summary").
report() {
  local mode=$1 loc_max with_locations max
  if [ "${mode}" == "zulip" ]; then
    loc_max=${locations_max_count}
    with_locations=$([ "${locations_max_count}" -gt 0 ] && echo true || echo false)
    max=${max_rows}
  else
    loc_max=1000000000
    with_locations=true
    max=0
  fi

  if (( ${#counts[@]} == 0 )); then
    echo "${header} [${ended} without messages](${run_url})."
    [ -n "${description}" ] && echo "${description}"
  else
    echo "${header} [${ended} with messages](${run_url}):"
    [ -n "${description}" ] && echo "${description}"
    printf '\n* %s' "${counts[@]}"
    printf '\n\n'
  fi

  if [ -n "${panic_lines}" ]; then
    echo "\`\`\`spoiler Panic counts"
    table "$(tabulate "${panic_lines}" "${loc_max}")" "Panic description" "${with_locations}" "${max}"
    echo "\`\`\`"
    echo
  fi

  if [ -n "${error_lines}" ]; then
    echo "\`\`\`spoiler Error counts"
    table "$(tabulate "${error_lines}" "${loc_max}")" "Error description" "${with_locations}" "${max}"
    echo "\`\`\`"
    echo
  fi

  if [ -n "${warning_lines}" ]; then
    echo "\`\`\`spoiler Warning counts"
    table "$(tabulate "${warning_lines}" "${loc_max}")" "Warning description" "${with_locations}" "${max}"
    echo "\`\`\`"
    echo
  fi

  if [ -n "${info_lines}" ] && [ "${info}" != "false" ]; then
    echo "\`\`\`spoiler Info message counts"
    table "$(tabulate "${info_lines}" "${loc_max}")" "Info message" "${with_locations}" "${max}"
    echo "\`\`\`"
    echo
  fi
}

delimiter=$(cat /proc/sys/kernel/random/uuid)
echo "zulip-message<<${delimiter}"
report zulip
echo "${delimiter}"

if [ -n "${GITHUB_STEP_SUMMARY}" ]; then
  # The `spoiler` blocks are Zulip syntax; GitHub renders `<details>` instead.
  # shellcheck disable=SC2016 # The backticks are literal Markdown.
  report summary |
    sed -E 's/^```spoiler (.*)$/<details><summary>\1<\/summary>\n/; s/^```$/\n<\/details>/' \
    >> "${GITHUB_STEP_SUMMARY}"
fi
