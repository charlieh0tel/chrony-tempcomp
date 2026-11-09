#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether the filter beats the unfiltered sensor, replayed on raw
recordings.

Usage:
  filter_gain.py RUN.csv ... --sensor PATH --line T0 K0 K1 K2 \
      [--taus SECONDS ...] [--exclude FROM:TO ...]

The truth is the crystal's own frequency each minute, from PPS against
CLOCK_MONOTONIC_RAW.  The recorder's 1 Hz sensor readings are replayed as
chronyd reads them: an instant reading every `--interval` s, through the
`tempcomp` line (`--line`, in the sensor's units, as chrony.conf has it).
Filtered, the reading is taken every `--period` s through a first-order lag
of each time constant, as tempcomp-filter.sh does, and chronyd reads the
filter's output instead; a time constant of 0 is the unfiltered sensor.

A perfect line holds the frequency plus its compensation constant
(chrony_tempcomp.py: comp = fref - own), so what is left of that, less its
median, is the error.  Each scheme is scored on the same minutes: its RMS,
day by day, and the time error the error would build in holdover, from
each hour's start, with chronyd's frequency at the loss taken as the mean
error of the 15 minutes before it.  And the noise each pushes into chronyd:
the RMS of the compensation's change from one of chronyd's readings to the
next, a frequency step its loop then chases, which the per-minute fit does
not see.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import numpy as np
from pps_step import load_run
from raw_minutes import exclude, place, read_piece, span
from room_days import MILLIDEGREES, SECONDS_PER_DAY, STEP, rms

# Seconds between chronyd's readings (the tempcomp line's interval) and
# the filter's, as on draws.
INTERVAL = 15.0
PERIOD = 5.0
# Restart the filter from the reading after a gap of this many time
# constants, as tempcomp-filter.sh does.
GAP_TAUS = 10
TAUS = (0, 60, 120, 180, 300, 450, 600, 900)
# Holdover: hours held, minutes before the loss whose mean chronyd's
# frequency stands for, and the share of a span's minutes needed.
HOLDOVER_HOURS = (1, 4, 24)
ESTIMATE_MINUTES = 15
COVERAGE = 0.9
SECONDS_PER_HOUR = 3600
PPB_PER_PPM = 1000


def compensation(line: Sequence[float], value: np.ndarray) -> np.ndarray:
    """chrony's tempcomp formula, ppm (`get_tempcomp`, `tempcomp.c`)."""
    t0, k0, k1, k2 = line
    d = value - t0
    return k0 + d * k1 + d * d * k2


def sample(times: np.ndarray, values: np.ndarray, ticks: np.ndarray) -> np.ndarray:
    """The last value at or before each tick, NaN before the first."""
    index = np.searchsorted(times, ticks, side="right") - 1
    return np.where(index >= 0, values[np.maximum(index, 0)], np.nan)


def ticks_over(times: np.ndarray, every: float) -> np.ndarray:
    return np.arange(times[0], times[-1], every)


def filtered(
    times: np.ndarray, readings: np.ndarray, tau: float, period: float = PERIOD
) -> tuple[np.ndarray, np.ndarray]:
    """(the filter's ticks, its output at each): the last reading every
    `period` s, unreadable ones skipped, through a first-order lag."""
    known = ~np.isnan(readings)
    ticks = ticks_over(times, period)
    taken = sample(times[known], readings[known], ticks)
    output = np.full(len(ticks), np.nan)
    f, then = np.nan, -np.inf
    for i, (tick, reading) in enumerate(zip(ticks, taken, strict=True)):
        if np.isnan(reading):
            output[i] = f
            continue
        if np.isnan(f) or tau <= 0 or tick - then > GAP_TAUS * tau:
            f = reading
        else:
            f += (1 - np.exp(-(tick - then) / tau)) * (reading - f)
        then = tick
        output[i] = f
    return ticks, output


def chronyd_reads(
    times: np.ndarray, readings: np.ndarray, tau: float, interval: float = INTERVAL
) -> tuple[np.ndarray, np.ndarray]:
    """(chronyd's reading times, the value it reads at each): the sensor
    (tau 0) or the filter's output, every `interval` s."""
    if tau > 0:
        times, readings = filtered(times, readings, tau)
    known = ~np.isnan(readings)
    ticks = ticks_over(times, interval)
    return ticks, sample(times[known], readings[known], ticks)


def applied(
    times: np.ndarray,
    readings: np.ndarray,
    line: Sequence[float],
    tau: float,
    interval: float = INTERVAL,
) -> tuple[np.ndarray, np.ndarray]:
    """(chronyd's reading times, the compensation it applies at each)."""
    ticks, values = chronyd_reads(times, readings, tau, interval)
    return ticks, compensation(line, values)


def minute_mean(ticks: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """The mean of the values in each grid minute, NaN where none."""
    minute = ((ticks - grid[0]) // STEP).astype(int)
    inside = (minute >= 0) & (minute < len(grid)) & ~np.isnan(values)
    total = np.bincount(minute[inside], values[inside], len(grid))
    count = np.bincount(minute[inside], minlength=len(grid))
    with np.errstate(invalid="ignore"):
        return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def minute_compensation(
    times: np.ndarray,
    readings: np.ndarray,
    line: Sequence[float],
    tau: float,
    grid: np.ndarray,
    interval: float = INTERVAL,
) -> np.ndarray:
    """The mean compensation chronyd applies in each grid minute."""
    return minute_mean(*applied(times, readings, line, tau, interval), grid)


def side_by_side(pieces: Sequence[np.ndarray]) -> np.ndarray:
    """One grid array from each file's, the first known value at each minute."""
    combined = np.full(len(pieces[0]), np.nan)
    for piece in pieces:
        combined = np.where(np.isnan(combined), piece, combined)
    return combined


def steps(
    times: np.ndarray,
    readings: np.ndarray,
    line: Sequence[float],
    tau: float,
    grid: np.ndarray,
    good: np.ndarray,
) -> np.ndarray:
    """The compensation's change, ppm, between chronyd's successive readings
    in the grid's good minutes."""
    ticks, applied_then = applied(times, readings, line, tau)
    change = np.diff(applied_then)
    minute = ((ticks[1:] - grid[0]) // STEP).astype(int)
    inside = (minute >= 0) & (minute < len(grid))
    inside[inside] &= good[minute[inside]]
    return change[inside & ~np.isnan(change)]


def holdover_errors(error: np.ndarray, hours: int) -> np.ndarray:
    """Time error, us (ppm times seconds), after `hours` held from each
    hour's start: the error's integral less the frequency chronyd held, the
    mean of the minutes before."""
    span_minutes = hours * SECONDS_PER_HOUR // STEP
    starts = range(
        ESTIMATE_MINUTES, len(error) - span_minutes, SECONDS_PER_HOUR // STEP
    )
    results = []
    for start in starts:
        before = error[start - ESTIMATE_MINUTES : start]
        held = error[start : start + span_minutes]
        known = ~np.isnan(held)
        if np.isnan(before).all() or known.mean() < COVERAGE:
            continue
        drift = np.nanmean(before)
        filled = np.interp(np.arange(len(held)), np.flatnonzero(known), held[known])
        results.append(np.sum(filled - drift) * STEP)
    return np.array(results)


def installed(
    grid: np.ndarray,
    pieces: Sequence[tuple[np.ndarray, np.ndarray]],
    line: Sequence[float],
    taus: Sequence[float],
) -> tuple[dict[float, np.ndarray], dict[float, Sequence[float]]]:
    """(each time constant's compensation per grid minute, its line): the
    one line for all."""
    comps = {
        tau: side_by_side(
            [
                minute_compensation(times, readings, line, tau, grid)
                for times, readings in pieces
            ]
        )
        for tau in taus
    }
    return comps, dict.fromkeys(taus, line)


def report(
    grid: np.ndarray,
    frequency: np.ndarray,
    pieces: Sequence[tuple[np.ndarray, np.ndarray]],
    comps: dict[float, np.ndarray],
    lines: dict[float, Sequence[float]],
) -> None:
    """Scores each time constant's compensation per grid minute, with its
    line for the steps."""
    good = ~np.isnan(frequency)
    for comp in comps.values():
        good &= ~np.isnan(comp)
    day = ((grid - grid[0]) // SECONDS_PER_DAY).astype(int)
    days = list(np.unique(day[good]))
    print(f"{good.sum() / 1440:.2f} days of minutes, {len(days)} days started")
    print(
        "  tau s  RMS ppm  by day ppm"
        + " " * max(0, 7 * len(days) - 10)
        + "".join(f"  {h:2d} h us" for h in HOLDOVER_HOURS)
        + "  step ppb"
    )
    for tau, comp in comps.items():
        error = np.where(good, frequency + comp, np.nan)
        error -= np.nanmedian(error)
        by_day = " ".join(f"{rms(error[good & (day == d)]):.4f}" for d in days)
        holdover = ""
        for hours in HOLDOVER_HOURS:
            held = holdover_errors(error, hours)
            holdover += f"  {rms(held):7.1f}" if len(held) else "        -"
        changes = np.concatenate(
            [
                steps(times, readings, lines[tau], tau, grid, good)
                for times, readings in pieces
            ]
        )
        noise = rms(changes) * PPB_PER_PPM
        print(
            f"  {tau:5.0f}   {rms(error[good]):.4f}   {by_day}{holdover}  {noise:8.2f}"
        )


def read_readings(path: str, sensor: str) -> tuple[np.ndarray, np.ndarray]:
    """(Unix seconds, the sensor in its own units) of each row read."""
    run = load_run(path)
    return run.unix_read / 1e9, run.sensors[sensor] * MILLIDEGREES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="pps_raw.py CSV files")
    parser.add_argument("--sensor", required=True, help="chronyd's tempcomp sensor")
    parser.add_argument(
        "--line", nargs=4, type=float, required=True, metavar=("T0", "K0", "K1", "K2")
    )
    parser.add_argument("--taus", nargs="+", type=float, default=TAUS)
    parser.add_argument("--exclude", nargs="*", type=span, default=[], help="FROM:TO")
    args = parser.parse_args()
    arrays = place([read_piece(path, args.sensor) for path in args.runs])
    exclude(arrays, args.exclude)
    pieces = [read_readings(path, args.sensor) for path in args.runs]
    grid = arrays["at"]
    comps, lines = installed(grid, pieces, args.line, args.taus)
    report(grid, arrays["frequency"], pieces, comps, lines)


if __name__ == "__main__":
    main()
