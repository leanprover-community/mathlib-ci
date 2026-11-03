#!/bin/bash
shopt -s extglob

lean_outfile=$1
target_repo=${TARGET_REPO:-${REPO:-${GITHUB_REPOSITORY}}}
target_sha=${TARGET_SHA:-${SHA:-${GITHUB_SHA}}}
workflow_repo=${WORKFLOW_REPO:-${REPO:-${GITHUB_REPOSITORY}}}
workflow_run_id=${WORKFLOW_RUN_ID:-${RUN_ID:-${GITHUB_RUN_ID}}}
workflow_name=${WORKFLOW:-${GITHUB_WORKFLOW}}
info=${INFO:-false}
# Opt-in: list every message with its location, instead of counting identical messages.
locations=${LOCATIONS:-false}
# Zulip truncates messages longer than this many characters.
max_message_length=${MAX_MESSAGE_LENGTH:-10000}
# Opt-in: break the counts down by linter, and report docstring warnings separately, by file.
by_linter=${BY_LINTER:-false}

# Skip the lines about build progress.
filtered_out=$(grep -v '^✔' "${lean_outfile}" | grep -v '^trace: ')
echo "$(wc -l <<<"${filtered_out}") lines of output" >&2

if [ "${by_linter}" == "true" ]; then
  # Tag the first line of each warning and info message with its linter, or "other",
  # read from Lean's closing "This linter can be disabled with `set_option linter.foo false`".
  tagged=$(awk '
    function flush() { if (msg != "") print linter "\t" msg; msg = "" }
    /^(error|warning|info): / { flush(); if ($0 !~ /^error: |PANIC at /) msg = $0; linter = "other" }
    sub(/.*This linter can be disabled with `set_option linter\./, "") { linter = $1 }
    END { flush() }' <<<"${filtered_out}")
  # Docstring syntax warnings are numerous and rarely urgent, so take them out of the other counts.
  docstring_lines=$(sed -n 's/^style\.docStringVerso\t\(warning: \)/\1/p' <<<"${tagged}")
  # No trailing newline: with no docstring warnings, an empty pattern would remove every blank line.
  filtered_out=$(grep -vxFf <(printf '%s' "${docstring_lines}") <<<"${filtered_out}")
  docstring_files=$(sed 's/^warning: \(\.\/\)*\([^:]*\):.*/\2/' <<<"${docstring_lines}" | sort | uniq -c | sort -bgr)
  # Sub-bullets counting the remaining messages of severity $1 per linter.
  # shellcheck disable=SC2016 # The backticks are Markdown.
  linter_counts() {
    sed -n "s/^\([^\t]*\)\t$1: .*/\1/p" <<<"${tagged}" | grep -vx 'style\.docStringVerso' |
      sort | uniq -c | sort -bgr | sed 's/^ *\([0-9]*\) \(.*\)$/  * `\2`: \1/'
  }
  warning_linters=$(linter_counts warning)
  info_linters=$(linter_counts info)
fi

delimiter=$(cat /proc/sys/kernel/random/uuid)
echo "zulip-message<<${delimiter}"

# Categorize the output.
counts=()
# Panics are reported as an info message.
# They should be very prominent when debugging, so treat them differently.
if panic_lines=$(grep '^info: .*PANIC at ' <<<"${filtered_out}"); then
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  panic_descriptions=$(sed 's/^info: [^:]*:[0-9]*:[0-9]*: //' <<<"${panic_lines}")
  counts+=( "$(printf 'Panics: %d' "$(wc -l <<<"${panic_lines}")")" )
  echo "$(wc -l <<<"${panic_lines}") lines of panic" >&2
fi
if error_lines=$(grep '^error: ' <<<"${filtered_out}"); then
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  error_descriptions=$(sed 's/^error: [^:]*:[0-9]*:[0-9]*: //' <<<"${error_lines}")
  counts+=( "$(printf 'Errors: %d' "$(wc -l <<<"${error_lines}")")" )
  echo "$(wc -l <<<"${error_lines}") lines of errors" >&2
fi
if [ -n "${docstring_lines}" ]; then
  counts+=( "Docstring warnings: $(wc -l <<<"${docstring_lines}") in $(wc -l <<<"${docstring_files}") files" )
fi
if warning_lines=$(grep '^warning: ' <<<"${filtered_out}"); then
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  warning_descriptions=$(sed 's/^warning: [^:]*:[0-9]*:[0-9]*: //' <<<"${warning_lines}")
  warning_count="$(printf 'Warnings: %d' "$(wc -l <<<"${warning_lines}")")${warning_linters:+$'\n'${warning_linters}}"
  if [ "${locations}" != "true" ]; then
    counts+=( "${warning_count}" )
  fi
  echo "$(wc -l <<<"${warning_lines}") lines of warnings" >&2
fi
if info_lines=$(grep '^info: ' <<<"${filtered_out}" | grep -v 'PANIC at '); then
  # shellcheck disable=SC2001 # The sed version is (hours!) faster than native Bash string manipulation.
  info_descriptions=$(sed 's/^info: [^:]*:[0-9]*:[0-9]*: //' <<<"${info_lines}")
  counts+=( "$(printf 'Info messages: %d' "$(wc -l <<<"${info_lines}")")${info_linters:+$'\n'${info_linters}}" )
  echo "$(wc -l <<<"${info_lines}") lines of info" >&2
fi
# With locations, the warnings table comes last (see below), so list its count last too.
if [ -n "${warning_count}" ] && [ "${locations}" == "true" ]; then
  counts+=( "${warning_count}" )
fi

if [ "true" == "${SUCCESS}" ]; then
  icon="✅"
  ended="succeeded"
else
  icon="❌"
  ended="failed"
fi

# With locations, the tables can outgrow a Zulip message, and Zulip would cut it off mid-table.
# Instead, drop the table rows that do not fit and say how many there were.
fit_message() {
  if [ "${locations}" != "true" ]; then cat; return; fi
  LC_ALL=C awk -v max="${max_message_length}" -v run="https://github.com/${workflow_repo}/actions/runs/${workflow_run_id}" '
    # Keep room for what is printed after the cut. Bytes are at least as many as characters.
    BEGIN { max -= 200 + 2 * length(run) }
    !cut && used + length($0) + 1 <= max {
      print; used += length($0) + 1
      if ($0 ~ /^```spoiler /) { open = 1; table = 0 }
      else if ($0 == "```") open = 0
      else if ($0 ~ /^\| -/) table = 1
      next
    }
    { cut = 1 }
    # Finish the table header, so that the table stays valid.
    open && !table { print; table = /^\| -/; next }
    open && /^\| / { rows++; next }
    open && $0 == "```" {
      if (rows) print "| … | " rows " more; see the [workflow run](" run ") |"
      print; open = 0; next
    }
    NF { lost = 1 }
    END { if (lost) print "\nSome messages were left out to fit the Zulip message size limit; see the [workflow run](" run ")." }'
}

# Print the messages whose first lines are $2 as a table of locations and messages, sorted by location,
# in a spoiler titled $1. Indented lines after a first line continue its message.
occurrences() {
  echo "\`\`\`spoiler $1 ($(wc -l <<<"$2"))"
  echo "| Location | Message |"
  echo "| --- | --- |"
  awk 'NR == FNR { want[$0]; next }
    function flush() { if (msg != "") print msg; msg = "" }
    $0 in want { flush(); msg = $0; sub(/^[a-z]+: (\.\/)*/, "", msg); next }
    msg != "" && /^ / { sub(/^ +/, " "); msg = msg $0; next }
    { flush() }
    END { flush() }' <(printf '%s\n' "$2") <(printf '%s\n' "${filtered_out}") |
    LC_ALL=C sort -t : -k1,1 -k2,2n -k3,3n |
    awk '
      # Escape `|` outside code spans, where it would end the table cell. After an unmatched backtick, it is not code.
      function cell(s,    n, part, i, out) {
        n = split(s, part, "`")
        for (i = 1; i <= n; i++) { if (i % 2 || i == n) gsub(/\|/, "\\|", part[i]); out = out (i > 1 ? "`" : "") part[i] }
        return out
      }
      /^[^:]*:[0-9]+:[0-9]+: / { i = index($0, ": "); print "| `" substr($0, 1, i - 1) "` | " cell(substr($0, i + 2)) " |"; next }
      { print "| | " cell($0) " |" }'
  echo "\`\`\`"
  echo
}

{
if (( ${#counts[@]} == 0 )); then
  echo "${icon} ${workflow_name} run on [${target_repo}](https://github.com/${target_repo}) (commit [${target_sha}](https://github.com/${target_repo}/commit/${target_sha})) [${ended} without messages](https://github.com/${workflow_repo}/actions/runs/${workflow_run_id})."
else
  echo "${icon} ${workflow_name} run on [${target_repo}](https://github.com/${target_repo}) (commit [${target_sha}](https://github.com/${target_repo}/commit/${target_sha})) [${ended} with messages](https://github.com/${workflow_repo}/actions/runs/${workflow_run_id}):"
  printf '\n* %s' "${counts[@]}"
  printf '\n\n'
fi

if [ -n "${panic_lines}" ] && [ "${locations}" == "true" ]; then
  occurrences "Panics" "${panic_lines}"
elif [ -n "${panic_lines}" ]; then
  echo "\`\`\`spoiler Panic counts"
  echo "| | Panic description |"
  echo "| ---: | --- |"
  sort <<<"${panic_descriptions}" | uniq -c | sort -bgr | sed 's/^\( *[0-9][0-9]*\) \(.*\)$/| \1 | \2 |/'
  echo "\`\`\`"
  echo
fi

if [ -n "${error_lines}" ] && [ "${locations}" == "true" ]; then
  occurrences "Errors" "${error_lines}"
elif [ -n "${error_lines}" ]; then
  echo "\`\`\`spoiler Error counts"
  echo "| | Error description |"
  echo "| ---: | --- |"
  sort <<<"${error_descriptions}" | uniq -c | sort -bgr | sed 's/^\( *[0-9][0-9]*\) \(.*\)$/| \1 | \2 |/'
  echo "\`\`\`"
  echo
fi

if [ -n "${docstring_lines}" ]; then
  echo "\`\`\`spoiler Docstring warnings by file"
  echo "| | File |"
  echo "| ---: | --- |"
  awk -v run="https://github.com/${workflow_repo}/actions/runs/${workflow_run_id}" '
    NR <= 10 { print "| " $1 " | `" $2 "` |" }
    END { if (NR > 10) print "| … | " NR - 10 " more files; see the [workflow run](" run ") |" }' <<<"${docstring_files}"
  echo "\`\`\`"
  echo
fi

# With locations, the warnings come last: they are usually the largest table, so `fit_message` cuts them first.
if [ -n "${warning_lines}" ] && [ "${locations}" != "true" ]; then
  echo "\`\`\`spoiler Warning counts"
  echo "| | Warning description |"
  echo "| ---: | --- |"
  sort <<<"${warning_descriptions}" | uniq -c | sort -bgr | sed 's/^\( *[0-9][0-9]*\) \(.*\)$/| \1 | \2 |/'
  echo "\`\`\`"
  echo
fi

if [ -n "${info_lines}" ] && [ "${info}" != "false" ] && [ "${locations}" == "true" ]; then
  occurrences "Info messages" "${info_lines}"
elif [ -n "${info_lines}" ] && [ "${info}" != "false" ]; then
  echo "\`\`\`spoiler Info message counts"
  echo "| | Info message |"
  echo "| ---: | --- |"
  sort <<<"${info_descriptions}" | uniq -c | sort -bgr | sed 's/^\( *[0-9][0-9]*\) \(.*\)$/| \1 | \2 |/'
  echo "\`\`\`"
  echo
fi

if [ -n "${warning_lines}" ] && [ "${locations}" == "true" ]; then
  occurrences "Warnings" "${warning_lines}"
fi
} | fit_message

echo "${delimiter}" >> "$GITHUB_OUTPUT"
