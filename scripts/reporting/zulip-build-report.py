#!/usr/bin/env python3

# This script summarizes a `lake build` log containing errors and warnings into
# a markdown message containing a table of errors and warnings and how many times they appeared.
# The script expects to be run in GitHub CI, but you can also run it locally:
#   ./scripts/reporting/technical-debt-metrics.py ./your-build-log.txt > ./summary.md
# To also include a summary of info messages, set the environment variable `INFO` to `true`.

import argparse
import collections
import os
import re
import sys
import uuid
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('build_log_filepath')
    args = parser.parse_args()

    # https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#multiline-strings
    delimiter = uuid.uuid4()
    print(f'zulip-message<<{delimiter}')
    print_zulip_message(build_log_filepath=Path(args.build_log_filepath))
    print(delimiter)

def print_zulip_message(build_log_filepath: Path) -> None:
    build_succeeded = get_env_bool('SUCCESS', default=False)
    show_info_messages = get_env_bool('INFO', default=False)

    target_repo = os.getenv('TARGET_REPO') or os.getenv('REPO') or os.getenv('GITHUB_REPOSITORY')
    target_sha = os.getenv('TARGET_SHA') or os.getenv('SHA') or os.getenv('GITHUB_SHA')
    workflow_repo = os.getenv('WORKFLOW_REPO') or os.getenv('REPO') or os.getenv('GITHUB_REPOSITORY')
    workflow_run_id = os.getenv('WORKFLOW_RUN_ID') or os.getenv('RUN_ID') or os.getenv('GITHUB_RUN_ID')
    workflow_name = os.getenv('WORKFLOW') or os.getenv('GITHUB_WORKFLOW')

    build_log_lines = build_log_filepath.read_text().splitlines()

    # Skip the lines about build progress.
    filtered_out = [line for line in build_log_lines if not (line.startswith('✔') or line.startswith('trace: '))]
    print(len(filtered_out), 'lines of output', file=sys.stderr)

    # Categorize the output.
    counts = []

    # Panics are reported as an info message.
    # They should be very prominent when debugging, so treat them differently.
    panic_lines = [line for line in filtered_out if line.startswith('info: ') and 'PANIC at ' in line]
    if panic_lines:
        counts.append(f'Panics: {len(panic_lines)}')
        print(len(panic_lines), 'lines of panic', file=sys.stderr)

    error_lines = [line for line in filtered_out if line.startswith('error: ')]
    if error_lines:
        counts.append(f'Errors: {len(error_lines)}')
        print(len(error_lines), 'lines of errors', file=sys.stderr)

    warning_lines = [line for line in filtered_out if line.startswith('warning: ')]
    if warning_lines:
        counts.append(f'Warnings: {len(warning_lines)}')
        print(len(warning_lines), 'lines of warnings', file=sys.stderr)

    info_lines = [line for line in filtered_out if line.startswith('info: ') and 'PANIC at ' not in line]
    if info_lines:
        counts.append(f'Info messages: {len(info_lines)}')
        print(len(info_lines), 'lines of info', file=sys.stderr)

    run_identifier = f'{workflow_name} run on [{target_repo}](https://github.com/{target_repo}) (commit [{target_sha}](https://github.com/{target_repo}/commit/{target_sha}))'
    run_url = f'https://github.com/{workflow_repo}/actions/runs/{workflow_run_id}'

    if build_succeeded:
        icon = '✅'
        ended = 'succeeded'
    else:
        icon = '❌'
        ended = 'failed'

    if counts:
        print(f'{icon} {run_identifier} [{ended} with messages]({run_url}):')
        print()
        for count in counts:
            print('*', count)
        print()
    else:
        print(f'{icon} {run_identifier} [{ended} without messages]({run_url}).')

    if panic_lines:
        print('```spoiler Panic counts')
        print('| | Panic description |')
        print('| ---: | --- |')
        panic_descriptions = [re.sub(r'^info: [^:]*:[0-9]*:[0-9]*: ', '', line) for line in panic_lines]
        panic_descriptions_counts = collections.Counter(panic_descriptions)
        for description, count in sorted(panic_descriptions_counts.items(), reverse=True, key=lambda item: (item[1], item[0])):
            print(f'| {count:7} | {description} |')
        print('```')
        print()

    if error_lines:
        print('```spoiler Error counts')
        print('| | Error description |')
        print('| ---: | --- |')
        error_descriptions = [re.sub(r'^error: [^:]*:[0-9]*:[0-9]*: ', '', line) for line in error_lines]
        error_descriptions_counts = collections.Counter(error_descriptions)
        for description, count in sorted(error_descriptions_counts.items(), reverse=True, key=lambda item: (item[1], item[0])):
            print(f'| {count:7} | {description} |')
        print('```')
        print()

    if warning_lines:
        print('```spoiler Warning counts')
        print('| | Warning description |')
        print('| ---: | --- |')
        warning_descriptions = [re.sub(r'^warning: [^:]*:[0-9]*:[0-9]*: ', '', line) for line in warning_lines]
        warning_descriptions_counts = collections.Counter(warning_descriptions)
        for description, count in sorted(warning_descriptions_counts.items(), reverse=True, key=lambda item: (item[1], item[0])):
            print(f'| {count:7} | {description} |')
        print('```')
        print()

    if info_lines and show_info_messages:
        print('```spoiler Info message counts')
        print('| | Info message |')
        print('| ---: | --- |')
        info_descriptions = [re.sub(r'^info: [^:]*:[0-9]*:[0-9]*: ', '', line) for line in info_lines]
        info_descriptions_counts = collections.Counter(info_descriptions)
        for description, count in sorted(info_descriptions_counts.items(), reverse=True, key=lambda item: (item[1], item[0])):
            print(f'| {count:7} | {description} |')
        print('```')
        print()

def get_env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key=key)
    if not value:
        return default
    if value == 'true':
        return True
    if value == 'false':
        return False
    raise Error(f'Unsupported value `{value}` for environment variable `{key}')

if __name__ == '__main__':
    main()
