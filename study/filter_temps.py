#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drop corrupt rows from a temps.csv sensor log.

The log is  <epoch>,<acpitz C>,<k10temp C>  at 1 Hz.  Crashes and power loss
leave lines merged by a truncated write and runs of NUL bytes, and clock steps
leave timestamps that go backwards.  Rows that don't match the format, or
whose timestamp isn't later than the last row kept, are dropped; a count of
each is written to stderr.
"""

import argparse
import re
import sys
from collections import Counter

ROW = re.compile(rb"(\d{10}),\d+\.\d{3},\d+\.\d{3}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", nargs="?", default="-", help="temps.csv (default stdin)")
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="report each dropped line"
    )
    args = ap.parse_args()

    stdin = args.input == "-"
    out = sys.stdout.buffer
    dropped = Counter()
    last = 0
    with open(
        sys.stdin.fileno() if stdin else args.input, "rb", closefd=not stdin
    ) as src:
        for lineno, line in enumerate(src, 1):
            line = line.rstrip(b"\r\n")
            if m := ROW.fullmatch(line):
                t = int(m[1])
                if t > last:
                    out.write(line + b"\n")
                    last = t
                    continue
                reason = "non-increasing timestamp"
            else:
                reason = "malformed"
            dropped[reason] += 1
            if args.verbose:
                print(f"{lineno}: {reason}: {line[:60]!r}", file=sys.stderr)

    for reason, n in sorted(dropped.items()):
        print(f"dropped {n} {reason} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
