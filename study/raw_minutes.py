#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw recordings as one-minute buckets in fetch.py's form, so the scripts
that read chronyd's readings read the crystal's own frequency instead.

Usage:
  raw_minutes.py RUN.csv ... --sensor PATH [--exclude FROM:TO ...] -o HOST.npz

Each `pps_raw.py` file is binned on its own, one minute a line through its
edges, and the files laid on one grid of minutes, NaN between them.
`frequency` is the raw frequency, positive fast, and `compensation` zero:
no compensation reaches CLOCK_MONOTONIC_RAW.  `--exclude` spans (Unix
seconds), widened by pps_cycles.UNUSABLE_MARGIN, are NaN: a reference
without a fix, or a load step and its settling.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import numpy as np
from pps_cycles import UNUSABLE_MARGIN
from pps_step import bin_run, load_run
from room_days import MILLIDEGREES, STEP


def place(
    pieces: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
) -> dict[str, np.ndarray]:
    """fetch.py's arrays from (Unix seconds, ppm, degrees C) per file, each
    bin put in the grid minute its center falls in."""
    start = min(at[0] for at, _, _ in pieces)
    start -= start % STEP
    end = max(at[-1] for at, _, _ in pieces)
    grid = start + STEP * np.arange((end - start) // STEP + 1)
    frequency = np.full(len(grid), np.nan)
    temperature = np.full(len(grid), np.nan)
    for at, ppm, sensor in pieces:
        index = ((at - start) // STEP).astype(int)
        frequency[index] = ppm
        temperature[index] = sensor * MILLIDEGREES
    return {
        "at": grid,
        "temperature": temperature,
        "frequency": frequency,
        "compensation": np.zeros(len(grid)),
    }


def exclude(
    arrays: dict[str, np.ndarray], spans: Sequence[tuple[float, float]]
) -> None:
    """NaN in the frequency over each span and its margin."""
    at = arrays["at"]
    for start, end in spans:
        inside = (at >= start - UNUSABLE_MARGIN) & (at <= end + UNUSABLE_MARGIN)
        arrays["frequency"][inside] = np.nan


def read_piece(path: str, sensor: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bins = bin_run(load_run(path), STEP)
    if sensor not in bins.sensors:
        raise SystemExit(f"{path}: no sensor {sensor}; it has {sorted(bins.sensors)}")
    known = ~np.isnan(bins.unix)
    return bins.unix[known], bins.ppm[known], bins.sensors[sensor][known]


def span(text: str) -> tuple[float, float]:
    start, _, end = text.partition(":")
    return float(start), float(end)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="pps_raw.py CSV files")
    parser.add_argument("--sensor", required=True, help="chronyd's tempcomp sensor")
    parser.add_argument("--exclude", nargs="*", type=span, default=[], help="FROM:TO")
    parser.add_argument("-o", dest="output", required=True)
    args = parser.parse_args()
    arrays = place([read_piece(path, args.sensor) for path in args.runs])
    exclude(arrays, args.exclude)
    # The arrays' names are savez's keywords; a dict is what its stub refuses.
    np.savez_compressed(args.output, allow_pickle=False, **arrays)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
