#!/usr/bin/env bash

# Backwards compatibility wrapper for the Python script that replaces the original shell script.
"$(dirname "$(realpath "$0")")/zulip-build-report.py" "$@"
