#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether the room's temperature predicts a host's frequency on held-out
days, beyond its sensor.

Usage:
  room_days.py reduce DB -o ROOM.npz
  room_days.py report ROOM.npz HOST.npz ...

Each whole day is predicted from the others, its lags chosen on the others
too, so the comparison is free of choosing on what is scored.  The readings
are chronyd's (`fetch.py`'s .npz), so its window is in them.
"""

from __future__ import annotations

import argparse
from collections.abc import Hashable, Sequence

import numpy as np
from common import MAD_SIGMA, design, lag
from pps_cycles import SECONDS_PER_MINUTE, read_ambient

SECONDS_PER_DAY = 86400
# Seconds per sample, as fetch.py's buckets and the room's reduced record.
STEP = 60
# A day with fewer good samples than this is not held out.
MIN_DAY_SAMPLES = 1200
# Samples kept out of a fit either side of its held-out day.
MARGIN = 60
# Residuals further than this many robust deviations are left out of a fit.
TRIM = 5.0
# Lags tried, in minutes.
SENSOR_LAGS = (0, 1, 2, 3, 5, 7, 10, 15, 20)
ROOM_LAGS = (0, 5, 10, 20, 30, 60, 90, 120, 180)
# Fewer held-out days than this say nothing.
MIN_DAYS = 3
MILLIDEGREES = 1000.0


def rms(error: np.ndarray) -> float:
    return float(np.sqrt(np.mean(error**2)))


def day_folds(
    at: np.ndarray, good: np.ndarray, margin: int = MARGIN
) -> list[tuple[np.ndarray, np.ndarray]]:
    """(fitted, held out) masks, one pair per whole day from the first
    sample, with `margin` samples either side of the day kept out of the fit."""
    if not len(at):
        return []
    day = ((at - at[0]) // SECONDS_PER_DAY).astype(int)
    folds = []
    for number in np.unique(day):
        held = good & (day == number)
        if held.sum() < MIN_DAY_SAMPLES:
            continue
        inside = np.flatnonzero(day == number)
        fitted = good.copy()
        fitted[max(0, inside[0] - margin) : inside[-1] + margin + 1] = False
        folds.append((fitted, held))
    return folds


def trimmed_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least squares, then again without the outliers: robust, and quick
    enough for every lag on every day."""
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    center = np.median(residual)
    deviation = MAD_SIGMA * np.median(np.abs(residual - center))
    keep = np.abs(residual - center) < TRIM * deviation
    return np.linalg.lstsq(x[keep], y[keep], rcond=None)[0]


def held_out_errors(
    x: np.ndarray, y: np.ndarray, folds: Sequence[tuple[np.ndarray, np.ndarray]]
) -> list[np.ndarray]:
    """One day's errors per fold."""
    return [
        y[held] - x[held] @ trimmed_fit(x[fitted], y[fitted]) for fitted, held in folds
    ]


def nested[Key: Hashable](
    table: dict[Key, list[np.ndarray]],
) -> tuple[list[np.ndarray], list[Key]]:
    """(each day's errors, its lags), by the entry of `table` whose errors
    are least on the other days."""
    days = len(next(iter(table.values())))
    errors, chosen = [], []
    for day in range(days):

        def others(key: Key, day: int = day) -> float:
            return float(
                np.mean([np.mean(table[key][d] ** 2) for d in range(days) if d != day])
            )

        best = min(table, key=others)
        chosen.append(best)
        errors.append(table[best][day])
    return errors, chosen


def compensation_spans(
    at: np.ndarray, compensation: np.ndarray
) -> dict[str, np.ndarray]:
    """Whole days by whether tempcomp was off or on, leaving out the day it
    came on.  chronyd's frequency less a compensation holds the raw reading
    through it, which favors no lag, so they are judged apart."""
    day = (at - at[0]) // SECONDS_PER_DAY
    active = np.flatnonzero(np.nan_to_num(compensation) != 0)
    if not len(active):
        return {"compensation off": np.ones(len(at), dtype=bool)}
    start = day[active[0]]
    return {"compensation off": day < start, "compensation on": day > start}


def report_span(
    label: str,
    at: np.ndarray,
    y: np.ndarray,
    temperature: np.ndarray,
    ambient: np.ndarray,
) -> None:
    good = ~np.isnan(y) & ~np.isnan(temperature)
    folds = day_folds(at, good)
    if len(folds) < MIN_DAYS:
        print(f"{label}: {len(folds)} whole days, too few")
        return
    known = ~np.isnan(temperature)
    sensor = np.interp(at, at[known], temperature[known])
    sensor = sensor - sensor[good].mean()
    room = ambient - ambient[good].mean()
    correlation = np.corrcoef(sensor[good], room[good])[0, 1]
    print(
        f"{label}: {len(folds)} whole days, sensor and room correlate {correlation:.3f}"
    )

    def filtered(values: np.ndarray, minutes: int) -> np.ndarray:
        return lag(values, minutes * SECONDS_PER_MINUTE, STEP)

    alone = {
        s: held_out_errors(design(filtered(sensor, s), True), y, folds)
        for s in SENSOR_LAGS
    }
    both = {
        (s, a): held_out_errors(
            np.column_stack([design(filtered(sensor, s), True), filtered(room, a)]),
            y,
            folds,
        )
        for s in SENSOR_LAGS
        for a in ROOM_LAGS
    }

    def total(errors: list[np.ndarray]) -> float:
        return rms(np.concatenate(errors))

    print("  held-out RMSE, ppm, by sensor lag in min: alone; with the room's best lag")
    for s in SENSOR_LAGS:
        score, a = min((total(both[(s, a)]), a) for a in ROOM_LAGS)
        print(f"    {s:3d}: {total(alone[s]):.4f}; {score:.4f} at {a}")
    alone_errors, alone_chosen = nested(alone)
    both_errors, both_chosen = nested(both)
    print(
        f"  lags chosen on the other days: sensor {total(alone_errors):.4f}"
        f" chose {sorted(set(alone_chosen))}; with the room {total(both_errors):.4f}"
        f" chose {sorted(set(both_chosen))}"
    )
    by_day_alone = np.array([rms(e) for e in alone_errors])
    by_day_both = np.array([rms(e) for e in both_errors])
    print(
        f"  the room is better on {int((by_day_both < by_day_alone).sum())}"
        f" of {len(folds)} days"
    )


def report(room_at: np.ndarray, room: np.ndarray, path: str, name: str) -> None:
    data = np.load(path)
    at, compensation = data["at"], data["compensation"]
    y = data["frequency"] - compensation
    temperature = data["temperature"] / MILLIDEGREES
    ambient = np.interp(at, room_at, room)
    print(f"== {name}")
    spans = {"all": np.ones(len(at), dtype=bool)} | compensation_spans(at, compensation)
    for label, k in spans.items():
        report_span(label, at[k], y[k], temperature[k], ambient[k])


def host_name(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".npz")


def reduce_room(args: argparse.Namespace) -> None:
    at, value = read_ambient(args.database)
    grid = np.arange(at[0] - at[0] % STEP, at[-1], STEP, dtype=float)
    np.savez_compressed(args.output, at=grid, value=np.interp(grid, at, value))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    reducing = commands.add_parser(
        "reduce", help="the room's record to one-minute samples"
    )
    reducing.add_argument("database", help="a copy of the sensor database")
    reducing.add_argument("-o", dest="output", required=True)
    reporting = commands.add_parser(
        "report", help="held-out days, with and without the room"
    )
    reporting.add_argument("room", help="a .npz from reduce")
    reporting.add_argument("hosts", nargs="+", help="fetch.py .npz files")
    args = parser.parse_args()
    if args.command == "reduce":
        reduce_room(args)
        return
    room = np.load(args.room)
    for path in args.hosts:
        report(room["at"], room["value"], path, host_name(path))


if __name__ == "__main__":
    main()
