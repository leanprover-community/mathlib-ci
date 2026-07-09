#!/usr/bin/env bash
{
# Backwards compatibility wrapper for the Python script that replaces the original shell script.
"$(dirname "$(realpath "$0")")/technical-debt-metrics.py" "$@"
# Note that we can't just write `./technical-debt-metrics.py` since it should look relative to the
# script's directory, not the current working directory.
}
