# SPDX-License-Identifier: GPL-3.0-or-later
"""chronyd's response to a step in its compensation, from its own logs.

A step in tempcomp's compensation with no change in the crystal (test 2's
switch at midnight UTC between smoothed and no correction) is a known step
in the kernel's frequency that chronyd is not told about (`LCL_SetTempComp`,
`local.c`, chrony 4.6).  tracking.log's frequency is the loop's with the
compensation undone (`LCL_ReadAbsoluteFrequency`), so it moves by the step
as the loop takes it in.  This reports how long that takes, the offset it
costs, and the regression's window from statistics.log, which sets how far
the loop lags a change too slow to trip its runs test.

    python3 chronyd_step.py /var/log/chrony --source GPS

Columns: tempcomp.log, tracking.log and statistics.log in chrony 4.6's
`chrony.conf.adoc` ("log" directive).
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import lzma
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

import numpy as np

# Columns of each log, counting the date and the time as two.
TEMPCOMP_COMPENSATION = 3
TRACKING_SOURCE, TRACKING_FREQUENCY, TRACKING_OFFSET = 2, 4, 6
STATISTICS_SOURCE, STATISTICS_SAMPLES = 2, 9
# Seconds of tracking before a step that set the frequency it left, and
# after it, the last part of which set the frequency it reached.
BEFORE, AFTER, SETTLED = 300.0, 900.0, 300.0
# Share of the smallest step the compensation may spread either side of
# one, for it to count.
STEADY = 0.25
# Fractions of the step at which the response is timed.
FRACTIONS = (0.1, 0.5, 0.9)
# Gaps between statistics rows longer than this, seconds, are not updates.
MAX_UPDATE = 120.0
PPB_PER_PPM = 1000.0
US_PER_S = 1e6
# How to open a compressed log, by its suffix; others are plain text.
OPENERS: dict[str, Callable[..., IO[str]]] = {".gz": gzip.open, ".xz": lzma.open}


def rows(directory: Path, name: str) -> Iterator[list[str]]:
    """Each data row of a log and its rotations, plain, gzipped or xz'd
    (as archived), split."""
    for path in sorted(directory.glob(name + ".log*")):
        opener = OPENERS.get(path.suffix, open)
        with opener(path, "rt", errors="replace") as log:
            for line in log:
                if line[:2] == "20":
                    yield line.split()


def utc(fields: list[str]) -> float:
    when = datetime.datetime.fromisoformat(fields[0] + " " + fields[1])
    return when.replace(tzinfo=datetime.UTC).timestamp()


def table(
    directory: Path,
    name: str,
    columns: tuple[int, ...],
    source: str = "",
    source_column: int = 0,
) -> np.ndarray:
    """Time and the given columns of a log, in time order, one row per
    time; only `source`'s rows when it is given."""
    found = {
        (utc(f),) + tuple(float(f[c]) for c in columns)
        for f in rows(directory, name)
        if not source or f[source_column] == source
    }
    return np.array(sorted(found)).reshape(-1, 1 + len(columns))


def steps(compensation: np.ndarray, minimum: float) -> list[tuple[float, float]]:
    """(time, size in ppm) of each change between readings of at least
    `minimum` ppm, with the compensation steady (spread under STEADY of
    `minimum`) for BEFORE seconds before it and AFTER after: raw readings'
    flips are left out."""
    t, value = compensation.T
    change = np.diff(value)

    def steady(span: np.ndarray) -> bool:
        return bool(span.any()) and np.ptp(value[span]) < STEADY * minimum

    return [
        (float(t[i + 1]), float(change[i]))
        for i in np.flatnonzero(np.abs(change) >= minimum)
        if steady((t >= t[i + 1] - BEFORE) & (t < t[i + 1]))
        and steady((t >= t[i + 1]) & (t < t[i + 1] + AFTER))
    ]


def response(tracking: np.ndarray, at: float) -> dict[str, float]:
    """How the frequency moved after a step at `at`: by how much, when it
    first passed each of FRACTIONS of that (seconds after the step), and
    the largest offset meanwhile (seconds)."""
    t, frequency, offset = tracking.T
    before = frequency[(t >= at - BEFORE) & (t < at)]
    span = (t >= at) & (t < at + AFTER)
    after_t, after = t[span], frequency[span]
    start = float(np.median(before))
    end = float(np.median(after[after_t >= at + AFTER - SETTLED]))
    share = (after - start) / (end - start)
    result = {"moved": end - start, "offset": float(np.max(np.abs(offset[span])))}
    for fraction in FRACTIONS:
        result[f"{fraction:.0%}"] = float(after_t[np.argmax(share >= fraction)] - at)
    return result


def window(statistics: np.ndarray) -> dict[str, float]:
    """The update interval and the samples the regression keeps."""
    gaps = np.diff(statistics[:, 0])
    samples = statistics[:, 1]
    update = float(np.median(gaps[gaps < MAX_UPDATE]))
    return {
        "update": update,
        "samples": float(np.median(samples)),
        "low": float(np.percentile(samples, 10)),
        "high": float(np.percentile(samples, 90)),
        "window": update * float(np.median(samples)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="chronyd's logdir, or a copy")
    parser.add_argument("--source", required=True, help="refid in the logs")
    parser.add_argument(
        "--min-step", type=float, default=100.0, help="smallest step, ppb"
    )
    args = parser.parse_args()
    compensation = table(args.directory, "tempcomp", (TEMPCOMP_COMPENSATION,))
    tracking = table(
        args.directory,
        "tracking",
        (TRACKING_FREQUENCY, TRACKING_OFFSET),
        args.source,
        TRACKING_SOURCE,
    )
    statistics = table(
        args.directory,
        "statistics",
        (STATISTICS_SAMPLES,),
        args.source,
        STATISTICS_SOURCE,
    )
    w = window(statistics)
    print(
        f"update {w['update']:.1f} s; samples median {w['samples']:.0f}"
        f" (10-90%: {w['low']:.0f}-{w['high']:.0f}); window {w['window']:.0f} s"
    )
    print("step UTC              step ppb  moved ppb  10% s  50% s  90% s  offset us")
    for at, size in steps(compensation, args.min_step / PPB_PER_PPM):
        r = response(tracking, at)
        when = datetime.datetime.fromtimestamp(at, datetime.UTC)
        print(
            f"{when:%Y-%m-%d %H:%M:%S}  {size * PPB_PER_PPM:+8.0f}  "
            f"{r['moved'] * PPB_PER_PPM:+9.0f}  "
            + "  ".join(f"{r[f'{x:.0%}']:5.0f}" for x in FRACTIONS)
            + f"  {r['offset'] * US_PER_S:9.1f}"
        )


if __name__ == "__main__":
    main()
