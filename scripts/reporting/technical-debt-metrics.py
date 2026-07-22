#!/usr/bin/env python3

# This script returns a tally of some technical debts in current Mathlib,
# reporting also the change with respect to the same counts in Mathlib from last week.
# It must be run from a `mathlib4` checkout, and invoked via its path in a separate
# `mathlib-ci` checkout. To run locally, from your `mathlib4` directory:
#   git clone https://github.com/leanprover-community/mathlib-ci.git ../mathlib-ci
#   ../mathlib-ci/scripts/reporting/technical-debt-metrics.py pr_summary

# This script should support older versions of Python since it will run on user computers.
# Python 3.6 is the current target, but it should become no newer than the oldest supported Python version: https://devguide.python.org/versions/

# Fixes and other intentional changes in behaviour from the shell script are marked with a comment starting "FIXED:".
# Preserved bugs are marked with a comment starting "TODO:".

from collections import OrderedDict
from datetime import datetime, timedelta
import itertools
import subprocess
import sys

def line_count(filename):
    """Count the number of lines in a file."""
    # Source: https://stackoverflow.com/questions/845058/how-to-get-the-line-count-of-a-large-file-cheaply-in-python
    with open(filename, "rb") as file:
        return sum(1 for _line in file)

def run_grep(regex, flags='', includes=None, excludes=None):
    """Return the occurrences of a regular expression across the path(s) in the Git repository.

    :param regex: A string holding a `git grep`-compatible regular expression.
    :param include:
        A list of *pathspec* strings representing paths to include in the search.
        To be searched, a path must match (a prefix of) *any* of the include patterns, while *all* of the exclude patterns must not match.
        If `includes` is empty (the default), all files or folders in the current working directory will be searched.
        For more details about the pathspec syntax, see the pathspec entry in gitglossary(7).
    :param excludes:
        A list of *pathspec* strings representing paths to exclude from the search.
        To be searched, a path must match (a prefix of) *any* of the include patterns, while *all* of the exclude patterns must not match.
        An exclude pattern starting with a colon `:` *does not* have special meaning (add it to the include list, appropriately negated, instead).
        The default value is `["scripts"]`.
        For more details about the pathspec syntax, see the pathspec entry in gitglossary(7).

    :return: A `subprocess.CompletedProcess` object.
    """

    # FIXED: Broken pathspec handling in the original script is fixed, in particular multiple in/excludes are now handled correctly.
    if includes is None:
        includes = ['*']
    if excludes is None:
        excludes = ["scripts"]
    pathspecs = [*includes, *(":^" + pathspec for pathspec in excludes)]

    return subprocess.run(["git", "grep", flags, '-e', regex, "--", *pathspecs], capture_output=True, encoding='utf-8', check=False)

def grep(*args, case_sensitive=True, **kwargs):
    """Return the occurrences of a regular expression across the path(s) in the Git repository.

    :param case_sensitive: If true (the default), the regex will be case sensitive, set to false to match case insensitive.

    See `run_grep` for the other parameters and their meaning.

    :return: A list of matching occurrences (only the match itself: no filename, line number or surrounding context).
    """
    if case_sensitive:
        flags = '-oh'
    else:
        flags = '-ohi'
    process = run_grep(*args, flags=flags, **kwargs)
    if process.returncode == 1: # "No matches found" is not a problem.
        return []
    process.check_returncode() # Other errors are a problem.
    return process.stdout.split("\n")[:-1] # Exclude empty last line.

def count_grep(*args, case_sensitive=True, **kwargs):
    """Count the number of occurrences of a regular expression across the path(s) in the Git repository.

    :param case_sensitive: If true (the default), the regex will be case sensitive, set to false to match case insensitive.

    See `run_grep` for the other parameters and their meaning.
    """
    if case_sensitive:
        flags = '-ch'
    else:
        flags = '-chi'
    process = run_grep(*args, flags=flags, **kwargs)
    if process.returncode == 1: # "No matches found" is not a problem.
        return 0
    process.check_returncode() # Other errors are a problem.
    return sum(int(count) for count in process.stdout.split("\n")[:-1])

def count_tech_debt():
    backwards_compat_settings = sorted(set(grep('set_option backward\\.[A-Za-z0-9_.]*')))
    backwards_compat_flags = {}
    for set_option_line in backwards_compat_settings:
        assert set_option_line.startswith('set_option ')
        option = set_option_line[len('set_option '):]
        backwards_compat_flags[option] = count_grep(f'set_option {option} ')

    dsimp_plus_instances = count_grep("dsimp \\([+-][^ ]* *\\)*+instances")
    simp_plus_instances = count_grep("simp \\([+-][^ ]* *\\)*+instances") - dsimp_plus_instances

    deprecated_file_names = subprocess.run(["git", "ls-files", "**/Deprecated/*.lean"], capture_output=True, encoding='utf-8', check=True).stdout.split("\n")[:-1]
    deprecated_files = {filename: line_count(filename) for filename in sorted(deprecated_file_names)}

    # FIXED: reordered according to Kim's list of tech debt priorities.
    strong_tech_debt = {
        **backwards_compat_flags,
        "erw": count_grep("erw \\["),
        "adaptation notes": count_grep("^[· ]*#adaptation_note", excludes=["scripts", "Mathlib/Tactic/AdaptationNote.lean", "Mathlib/Tactic/Linter"]),
        "maxHeartbeats modifications": count_grep("^ *set_option .*maxHeartbeats.* [0-9][0-9]*", excludes=["scripts", "MathlibTest"]),
        "CommRing (Fin n) instances": count_grep("^open Fin.CommRing "),
        "NatCast (Fin n) instances": count_grep("^open Fin.NatCast "),
        "disabled simpNF lints": count_grep("nolint simpNF"),
        "proofs using autogenerated auxiliary lemmas": count_grep("set_option linter.auxLemma", excludes=["scripts", "MathlibTest"]),
        "disabled varHead simp warnings": count_grep("set_option warning.simp.varHead false", includes=["Mathlib", "Archive", "Counterexamples"], excludes=[]),
        # FIXED: referencing deprecated declarations inside deprecated declarations no longer raises a linter warning,
        # so we do not count those "double deprecations".
        "disabled deprecation lints": count_grep("set_option linter.deprecated false", excludes=["Mathlib/Deprecated"]), # The scripts folder is counted too, here!
        "documentation nolints entries": count_grep("docBlame", includes=['scripts/nolints.json'], excludes=[]),
        "misnamed declarations: definition names with an underscore": count_grep("defsWithUnderscore", includes=['scripts/nolints.json'], excludes=[]),
        "undocumented tactics": count_grep("tacticDocs", includes=['scripts/nolints.json'], excludes=[]),
        "exceptions for the docPrime linter": line_count('scripts/nolints_prime_decls.txt'),
        "porting notes": count_grep("Porting note", case_sensitive=False),
    }

    weak_tech_debt = {
        "flexible linter exceptions": count_grep("set_option linter.flexible", excludes=["scripts", "MathlibTest"]),
        "disabled overlappingInstances linter": count_grep("set_option linter.overlappingInstances", excludes=["scripts", "MathlibTest"]),
        "simp +instances": simp_plus_instances,
        "dsimp +instances": dsimp_plus_instances,
        "large files": count_grep('^set_option linter.style.longFile [0-9]*', includes=['Mathlib'], excludes=[]),
        "`Deprecated` files": len(deprecated_files),
        "total LoC in `Deprecated` files": sum(deprecated_files.values()),
        "exposed public sections": count_grep("^@\\[expose\\] public \\(meta \\|noncomputable \\)*section"),
    }
    return strong_tech_debt, weak_tech_debt, deprecated_files

def diff_counts(new, old):
    result = {}
    # Ensure we iterate over both old and new keys, since it is not guaranteed `count_tech_debt`
    # returns the same set of keys.
    for key in itertools.chain(new.keys(), old.keys()):
        new_val = new.get(key, 0)
        result[key] = (new_val, new_val - old.get(key, 0))
    return result

def diff_tech_debt(curr_commit, ref_commit):
    try:
        subprocess.run(["git", "switch", "-q", "--detach", curr_commit], check=True)
        new_strong, new_weak, new_deprecated = count_tech_debt()
    finally:
        subprocess.run(["git", "switch", "-q", "--detach", "-"], check=False)
    try:
        subprocess.run(["git", "switch", "-q", "--detach", ref_commit], check=True)
        old_strong, old_weak, old_deprecated = count_tech_debt()
    finally:
        subprocess.run(["git", "switch", "-q", "--detach", "-"], check=False)

    return diff_counts(new_strong, old_strong), diff_counts(new_weak, old_weak), diff_counts(new_deprecated, old_deprecated)

def markdown_table(headers, align, rows):
    """Render a Markdown table with given rows.

    :param headers: Header row.
    :param align: List of 'l'/'c'/'r', corresponding to left/center/right alignment. Should be same length as `headers`.
    :param rows: Iterable of lists of strings, each list of which should be as long as the header row.
    """
    def row(cells):
        assert len(cells) == len(headers)
        # TODO: markdown escaping.
        return f'|{"|".join(map(str, cells))}|'
    def to_underline(spec):
        try:
            return {'l': ':-', 'c': ':-:', 'r': '-:'}[spec]
        except KeyError:
            raise ValueError(f"alignment specifier should be 'l', 'c', 'r', got {spec}")
    lines = [row(headers), row(list(map(to_underline, align)))]
    lines.extend(row(r) for r in rows)
    return '\n'.join(lines)

def pr_summary_table(level, diffs):
    total = 0
    weight = 0
    absWeight = 0
    changes = False
    for key, (new, diff) in diffs.items():
        absWeight += diff
        if diff != 0:
            changes = True
        if new != 0:
            total += 1 / new
            weight += diff / new
    if total == 0:
        average = absWeight
    else:
        average = weight / total
    if average < 0:
        change = "Decrease"
        average = -average
        weight = -weight
    else:
        change = "Increase"

    if not changes:
        return f"No changes to {level} technical debt."
    else:
        table = markdown_table(["Current number", "Change", f"Type ({level})"], "rcl", ((key, new, diff) for key, (new, diff) in diffs.items()))
        return f"<details><summary>{change} in {level} tech debt: (relative, absolute) = ({average:4.2f}, {weight:4.2f})</summary>\n\n{table}\n</details>"

def footer(curr_commit, ref_commit):
    base_url = 'https://github.com/leanprover-community/mathlib4/commit'
    curr_link = f'[{curr_commit[:10]}]({base_url}/{curr_commit})'
    ref_link = f'[{ref_commit[:10]}]({base_url}/{ref_commit})'
    return f'''
Current commit {curr_link}
Reference commit {ref_link}
'''

def pr_summary_report(strong_tech_debt, weak_tech_debt, deprecated_files):
    strong_tech_debt = {key: (new, diff) for key, (new, diff) in strong_tech_debt.items() if diff != 0}
    weak_tech_debt = {key: (new, diff) for key, (new, diff) in weak_tech_debt.items() if diff != 0}

    strong_table = pr_summary_table("strong", strong_tech_debt)
    weak_table = pr_summary_table("weak", weak_tech_debt)
    return f"{strong_table}\n{weak_table}\n"

def zulip_report(strong_tech_debt, weak_tech_debt, deprecated_files):
    strong_table = markdown_table(["Current number", "Change", "Type (strong)"], "rcl", ((new, diff, key) for key, (new, diff) in strong_tech_debt.items()))
    weak_table = markdown_table(["Current number", "Change", "Type (weak)"], "rcl", ((new, diff, key) for key, (new, diff) in weak_tech_debt.items()))
    files_table = markdown_table(["LoC", "Change", "File"], "rcl", ((new, diff, key) for key, (new, diff) in deprecated_files.items()))

    return f"""{strong_table}

{weak_table}

```spoiler Changed 'Deprecated' lines per file
{files_table}
```
"""

def tech_debt_report(pr_summary, curr_commit, ref_commit):
    strong_tech_debt, weak_tech_debt, deprecated_files = diff_tech_debt(curr_commit, ref_commit)

    if pr_summary:
        # FIXED: the instructions say to run the Python script, not the shell script.
        return pr_summary_report(strong_tech_debt, weak_tech_debt, deprecated_files) + footer(curr_commit, ref_commit) + '''
This script lives in the [`mathlib-ci`](https://github.com/leanprover-community/mathlib-ci) repository. To run it locally, from your `mathlib4` directory:
```
git clone https://github.com/leanprover-community/mathlib-ci.git ../mathlib-ci
../mathlib-ci/scripts/reporting/technical-debt-metrics.py pr_summary
```
* The `relative` value is the weighted *sum* of the differences with weight given by the *inverse* of the current value of the statistic.
* The `absolute` value is the `relative` value divided by the total sum of the inverses of the current values (i.e. the weighted *average* of the differences).
'''
    else:
        return zulip_report(strong_tech_debt, weak_tech_debt, deprecated_files) + footer(curr_commit, ref_commit)

if len(sys.argv) > 1 and sys.argv[1] == "pr_summary":
    pr_summary = True
    curr_commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, encoding='utf-8', check=True).stdout.strip()
    # TODO: the latest version of leanprover-community/mathlib4 is called "upstream/master" for most users; can we determine the right remote automatically?
    ref_commit = subprocess.run(["git", "merge-base", "origin/master", "HEAD"], capture_output=True, encoding='utf-8', check=True).stdout.strip()
    print("***  pr_summary passed  ***", file=sys.stderr)
else:
    pr_summary = False
    curr_commit = sys.argv[1] if len(sys.argv) > 1 else \
        subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, encoding='utf-8', check=True).stdout.strip()
    last_week = datetime.now() - timedelta(days=7)
    ref_commit = sys.argv[2] if len(sys.argv) > 2 else \
        subprocess.run(["git", "log", "--pretty=%H", "--since", last_week.date().isoformat()], capture_output=True, encoding='utf-8', check=True).stdout.strip()\
            .split("\n")[-1] # Take only the first commit of that day
    print("***  NO pr_summary passed  ***", file=sys.stderr)

print(tech_debt_report(pr_summary, curr_commit, ref_commit))
