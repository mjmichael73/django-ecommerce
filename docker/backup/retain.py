#!/usr/bin/env python3
"""Retention selector for backup rotation.

A pure-function helper with NO I/O: it parses arguments and decides which
entries to keep vs. delete. The calling shell script owns the filesystem side
of rotation.

Usage:
    python3 retain.py [OPTIONS] [<entry> ...]

    Entries are filenames of the form ``<prefix>-<UTC timestamp>.<ext>``.
    The timestamp is extracted from each entry name (the longest ``-YYYYmmddHHMMSS``
    or ``YYYYmmddHHMMSS`` suffix) so entries can be ordered newest-first and
    only the newest ``keep_n`` are retained.

Options:
    --keep-n N        Number of newest entries to keep (default: 14).
    --dry-run         Print planned deletes to stderr; keep stdout clean for piping.
    --kind KIND       Optional label printed with dry-run plan (e.g. "db", "media", "redis").
    -h, --help        Show this help and exit.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import List, Sequence, Tuple

__all__ = ["parse_args", "select_retention", "extract_timestamp", "main"]

# Matches a trailing ``-YYYYmmddHHMMSS`` or bare ``YYYYmmddHHMMSS`` timestamp
# at the end of a filename stem (before the extension).
_TS_RE = re.compile(r"(?:.*-|.*)(\d{14})(?:\..*)?$")


def extract_timestamp(entry: str) -> str:
    """Return the sortable timestamp string embedded in *entry*.

    Falls back to the raw entry when no timestamp is found so ordering is
    still deterministic (lexicographic descent of the whole name).
    """
    m = _TS_RE.match(entry)
    return m.group(1) if m else entry


def select_retention(entries: Sequence[str], keep_n: int) -> Tuple[List[str], List[str]]:
    """Partition *entries* into (keep, delete) lists.

    Ordering is newest-first by the embedded timestamp, so the surviving
    ``keep`` list is ordered newest→oldest and the ``delete`` list is the
    complement oldest-first.
    """
    ordered = sorted(entries, key=extract_timestamp, reverse=True)
    keep = ordered[:keep_n]
    delete = ordered[keep_n:]
    return keep, delete


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Select which backup entries to keep vs. delete.",
        add_help=True,
    )
    p.add_argument(
        "--keep-n",
        type=int,
        default=14,
        help="number of newest entries to keep (default: 14)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned deletes to stderr instead of returning silently",
    )
    p.add_argument(
        "--kind",
        default="",
        help='optional label echoed with the dry-run plan (e.g. "db", "media")',
    )
    p.add_argument(
        "entries",
        nargs="*",
        help="backup filenames to evaluate (newest-first wins)",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    keep, delete = select_retention(args.entries, args.keep_n)
    if args.dry_run:
        kind = f" [{args.kind}]" if args.kind else ""
        for d in delete:
            sys.stderr.write(f"{kind} would delete: {d}\n")
        for k in keep:
            sys.stderr.write(f"{kind} would keep:    {k}\n")
    # Always emit the keep list to stdout (one per line) so callers may pipe.
    for k in keep:
        print(k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
