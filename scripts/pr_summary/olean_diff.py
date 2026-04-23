#!/usr/bin/env python3
"""Compare the .olean files of two Mathlib builds and write markdown reports.

In the module system, each module produces three olean files:
  Foo.olean          -- public/exported information (signatures, axioms)
  Foo.olean.server   -- docstrings, declaration ranges
  Foo.olean.private  -- full private data including proof bodies

Theorems are exported as axioms in the .olean, so changing only a proof body
should leave the public .olean byte-identical.
"""

import argparse
import filecmp
import os
import sys

GITHUB_COMMENT_LIMIT = 65000


def _error_comment(heading, body, actions_url=''):
    """Build a standard ## Olean diff error block."""
    parts = ['## Olean diff', '', f'**{heading}**', '', body]
    if actions_url:
        parts += ['', f'[CI run]({actions_url})']
    return '\n'.join(parts)


def has_oleans(lib_dir):
    """Return True if lib_dir contains at least one public .olean file."""
    if not os.path.isdir(lib_dir):
        return False
    for _, _, files in os.walk(lib_dir):
        for f in files:
            if f.endswith('.olean') and not (f.endswith('.olean.server') or
                                              f.endswith('.olean.private')):
                return True
    return False


def find_public_oleans(lib_dir):
    """Return a sorted list of .olean paths relative to lib_dir, excluding companions."""
    result = []
    for root, _, files in os.walk(lib_dir):
        for f in sorted(files):
            if f.endswith('.olean') and not (f.endswith('.olean.server') or f.endswith('.olean.private')):
                result.append(os.path.relpath(os.path.join(root, f), lib_dir))
    return sorted(result)


def olean_path_to_module(path):
    """Convert e.g. 'Mathlib/Algebra/Group/Defs.olean' to 'Mathlib.Algebra.Group.Defs'."""
    return path.replace(os.sep, '.').removesuffix('.olean')


def files_differ(path1, path2):
    exists1 = os.path.exists(path1)
    exists2 = os.path.exists(path2)
    if not exists1 and not exists2:
        return False
    if exists1 != exists2:
        return True
    try:
        return not filecmp.cmp(path1, path2, shallow=False)
    except OSError:
        return True


def classify_modules(base_dir, head_dir):
    base_set = set(find_public_oleans(base_dir))
    head_set = set(find_public_oleans(head_dir))

    removed = sorted(olean_path_to_module(f) for f in base_set - head_set)
    added = sorted(olean_path_to_module(f) for f in head_set - base_set)

    interface_changed = []
    nonpublic_changed = []

    for f in sorted(base_set & head_set):
        base_olean = os.path.join(base_dir, f)
        head_olean = os.path.join(head_dir, f)
        if files_differ(base_olean, head_olean):
            interface_changed.append(olean_path_to_module(f))
        elif (files_differ(base_olean + '.server', head_olean + '.server') or
              files_differ(base_olean + '.private', head_olean + '.private')):
            nonpublic_changed.append(olean_path_to_module(f))

    return added, removed, interface_changed, nonpublic_changed


def build_report(added, removed, interface_changed, nonpublic_changed,
                 omitted_interface=0, omitted_nonpublic=0):
    lines = ['## Olean diff', '']

    total_interface = len(interface_changed) + omitted_interface
    total_nonpublic = len(nonpublic_changed) + omitted_nonpublic

    if not total_interface and not added and not removed and not total_nonpublic:
        lines.append('No differences found.')
        return '\n'.join(lines)

    summary = []
    if total_interface:
        summary.append(f'{total_interface} module{"s" if total_interface != 1 else ""} with public interface changes')
    if added:
        summary.append(f'{len(added)} added')
    if removed:
        summary.append(f'{len(removed)} removed')
    if summary:
        lines.append(', '.join(summary) + '.')
        lines.append('')

    if interface_changed or omitted_interface:
        suffix = ' (truncated, see full report)' if omitted_interface else ''
        lines.append(f'<details><summary>{total_interface} module{"s" if total_interface != 1 else ""} with public interface changes{suffix}</summary>')
        lines.append('')
        lines.append('Exported signatures, declarations, or axioms changed.')
        lines.append('')
        lines += [f'- `{m}`' for m in interface_changed]
        if omitted_interface:
            lines.append(f'- … and {omitted_interface} more (see full report)')
        lines.append('')
        lines.append('</details>')
        lines.append('')

    if added:
        lines += ['<details><summary>Added modules</summary>', '']
        lines += [f'- `{m}`' for m in added]
        lines.append('')
        lines.append('</details>')
        lines.append('')

    if removed:
        lines += ['<details><summary>Removed modules</summary>', '']
        lines += [f'- `{m}`' for m in removed]
        lines.append('')
        lines.append('</details>')
        lines.append('')

    if total_nonpublic:
        suffix = ' (truncated, see full report)' if omitted_nonpublic else ''
        lines.append(f'<details><summary>{total_nonpublic} non-public change{"s" if total_nonpublic != 1 else ""}{suffix}</summary>')
        lines.append('')
        lines.append('Public `.olean` unchanged; proof bodies, docstrings, or declaration ranges changed.')
        lines.append('')
        lines += [f'- `{m}`' for m in nonpublic_changed]
        lines.append('')
        lines.append('</details>')

    return '\n'.join(lines)


def make_comment(added, removed, interface_changed, nonpublic_changed, limit=GITHUB_COMMENT_LIMIT):
    full = build_report(added, removed, interface_changed, nonpublic_changed)
    if len(full) <= limit:
        return full

    # Binary search: truncate non-public list first.
    lo, hi = 0, len(nonpublic_changed)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        r = build_report(added, removed, interface_changed, nonpublic_changed[:mid],
                         omitted_nonpublic=len(nonpublic_changed) - mid)
        if len(r) <= limit:
            lo = mid
        else:
            hi = mid - 1
    candidate = build_report(added, removed, interface_changed, nonpublic_changed[:lo],
                              omitted_nonpublic=len(nonpublic_changed) - lo)
    if len(candidate) <= limit:
        return candidate

    # Still too long: truncate interface list too.
    lo, hi = 0, len(interface_changed)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        r = build_report(added, removed, interface_changed[:mid], [],
                         omitted_interface=len(interface_changed) - mid,
                         omitted_nonpublic=len(nonpublic_changed))
        if len(r) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return build_report(added, removed, interface_changed[:lo], [],
                        omitted_interface=len(interface_changed) - lo,
                        omitted_nonpublic=len(nonpublic_changed))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('base_lib_dir', help='path to base .lake/build/lib/lean')
    p.add_argument('head_lib_dir', help='path to PR .lake/build/lib/lean')
    p.add_argument('comment_file', help='output path for truncated GitHub comment')
    p.add_argument('full_file', help='output path for complete report')
    p.add_argument('--merge-base-sha', default='', metavar='SHA',
                   help='merge base commit SHA (used in error messages)')
    p.add_argument('--base-ref', default='master', metavar='REF',
                   help='target branch name (used in error messages)')
    p.add_argument('--actions-url', default='', metavar='URL',
                   help='CI run URL (used in error messages)')
    args = p.parse_args()

    try:
        if not has_oleans(args.base_lib_dir):
            sha_short = args.merge_base_sha[:12] if args.merge_base_sha else 'unknown'
            msg = _error_comment(
                f'Oleans for the merge base (`{sha_short}`) are not available in the cache.',
                f'Try merging `{args.base_ref}` again.',
                args.actions_url,
            )
            with open(args.comment_file, 'w') as fh:
                fh.write(msg)
            sys.exit(0)

        added, removed, interface_changed, nonpublic_changed = classify_modules(
            args.base_lib_dir, args.head_lib_dir)

        full_report = build_report(added, removed, interface_changed, nonpublic_changed)
        comment = make_comment(added, removed, interface_changed, nonpublic_changed)

        with open(args.full_file, 'w') as fh:
            fh.write(full_report)
        with open(args.comment_file, 'w') as fh:
            fh.write(comment)

    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        msg = _error_comment(
            'The olean diff script encountered an unexpected error.',
            'This is a bug in the CI tooling.\n'
            'Please report it on [Zulip](https://leanprover.zulipchat.com).',
            args.actions_url,
        )
        try:
            with open(args.comment_file, 'w') as fh:
                fh.write(msg)
        except OSError:
            pass
        sys.exit(1)
