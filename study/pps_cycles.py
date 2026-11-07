#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A recording with repeated load cycles: the fast lag, and whether a model
frozen before the cycles predicts them.

Usage:
  pps_cycles.py bin RUN.csv --ambient DB --cycles LOG --first-off UNIX \
      [--unusable FROM:TO ...] -o HOST.npz
  pps_cycles.py report HOST.npz --sensor-lag MIN --ambient-lag MIN \
      --sensor-only-lag MIN

Every cycle crosses the same temperatures, so holding one out interpolates,
where a held-out fold of one step or of a trend would extrapolate.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

import numpy as np
from common import design, huber_fit, lag
from pps_step import bin_run, load_run

# Seconds per bin for the fast lag, and for the frozen models, which were
# chosen on these.  A longer bin is its own line through the edges: a mean of
# shorter bins' slopes keeps their noise.
BIN = 15
COARSE_BIN = 60
# The reading in smartclock-sensord's table that is the room's temperature.
AMBIENT_SENSOR_ID = 1
NANOSECONDS = 1e9
SECONDS_PER_MINUTE = 60.0
# Seconds kept off a span the reference was unusable.
UNUSABLE_MARGIN = 120.0
# Seconds after a load's end before the frozen models' quiet fit, and before
# the quiet stretch scored after the cycles.
SETTLE = 900.0
# Fast lags tried on the cycles, in minutes.
FAST_LAGS = (0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 7, 10, 15)
# The combinations the frozen models were chosen from, in minutes.
GRID_SENSOR_LAGS = (0, 1, 2, 3, 5)
GRID_AMBIENT_LAGS = (0, 5, 10, 20, 30, 60, 90, 120, 180)
# Minutes between rows of the folded cycle.
FOLD_ROW = 3


@dataclass
class Series:
    """A run in equal bins; one array element per bin, in Unix time."""

    at: np.ndarray  # Seconds at the bin's center.
    ppm: np.ndarray  # The crystal's frequency, NaN where unknown.
    sensor: np.ndarray  # chronyd's tempcomp sensor, degrees Celsius.
    ambient: np.ndarray  # The room, degrees Celsius.
    load_on: np.ndarray  # When each cycle's load started, Unix seconds.
    load_off: np.ndarray  # When it stopped.
    first_off: float  # The end of the one load step before the cycles.
    unusable: np.ndarray  # (from, to) of each span without a good reference.


@dataclass
class Score:
    """A model's error, ppm: `rms` about zero, `bias` its mean, `spread` the
    RMS left after the bias."""

    rms: float
    bias: float
    spread: float


def read_cycles(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(starts, ends) from a log of `on=UNIX` and `off=UNIX` lines."""
    times: dict[str, list[float]] = {"on": [], "off": []}
    with open(path) as file:
        for line in file:
            key, _, value = line.strip().partition("=")
            if key in times:
                times[key].append(float(value))
    if not times["on"] or len(times["on"]) != len(times["off"]):
        raise ValueError(f"{path}: {times}: want an end for each start")
    return np.array(times["on"]), np.array(times["off"])


def read_ambient(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(Unix seconds, degrees Celsius) of the room, from a copy of the
    sensor database."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT at, value FROM reading WHERE sensor_id = ? ORDER BY at",
            (AMBIENT_SENSOR_ID,),
        ).fetchall()
    finally:
        connection.close()
    readings = np.array(rows, dtype=float)
    return readings[:, 0] / NANOSECONDS, readings[:, 1]


def make_series(args: argparse.Namespace) -> None:
    run = load_run(args.run)
    ambient_at, ambient = read_ambient(args.ambient)
    load_on, load_off = read_cycles(args.cycles)
    arrays = {
        "load_on": load_on,
        "load_off": load_off,
        "first_off": np.array(args.first_off),
        "unusable": np.array(
            [[float(v) for v in span.split(":")] for span in args.unusable]
        ).reshape(-1, 2),
    }
    for width in (BIN, COARSE_BIN):
        bins = bin_run(run, width)
        sensor = next(iter(bins.sensors))
        known = ~np.isnan(bins.unix)
        at = bins.unix[known]
        arrays[f"at_{width}"] = at
        arrays[f"ppm_{width}"] = bins.ppm[known]
        arrays[f"sensor_{width}"] = bins.sensors[sensor][known]
        arrays[f"ambient_{width}"] = np.interp(at, ambient_at, ambient)
    # The arrays' names are savez's keywords; a dict is what its stub refuses.
    np.savez_compressed(args.output, allow_pickle=False, **arrays)  # type: ignore[arg-type]


def load_series(path: str, width: int) -> Series:
    """The series at one of the bin widths saved."""
    data = np.load(path)
    return Series(
        data[f"at_{width}"],
        data[f"ppm_{width}"],
        data[f"sensor_{width}"],
        data[f"ambient_{width}"],
        data["load_on"],
        data["load_off"],
        float(data["first_off"]),
        data["unusable"],
    )


def usable(series: Series) -> np.ndarray:
    """Which bins have a frequency from a good reference."""
    good = ~np.isnan(series.ppm) & ~np.isnan(series.sensor)
    for start, end in series.unusable:
        good &= (series.at < start - UNUSABLE_MARGIN) | (
            series.at > end + UNUSABLE_MARGIN
        )
    return good


def columns(sensor: np.ndarray, ambient: np.ndarray | None = None) -> np.ndarray:
    """The frequency's model: a curve on the sensor, plus a line on the room."""
    curve = design(sensor, True)
    return curve if ambient is None else np.column_stack([curve, ambient])


def fill(values: np.ndarray) -> np.ndarray:
    """From its mean, across unknown bins: the filter needs it throughout."""
    known = ~np.isnan(values)
    filled = np.interp(np.arange(len(values)), np.flatnonzero(known), values[known])
    return filled - filled.mean()


def score(error: np.ndarray) -> Score:
    bias = float(error.mean())
    return Score(
        float(np.sqrt(np.mean(error**2))),
        bias,
        float(np.std(error)),
    )


def fold_scores(x: np.ndarray, y: np.ndarray, folds: list[np.ndarray]) -> float:
    """Held-out RMSE over folds, each predicted from the rest."""
    errors = []
    for i, held in enumerate(folds):
        rest = np.logical_or.reduce([f for j, f in enumerate(folds) if j != i])
        errors.append(y[held] - x[held] @ huber_fit(x[rest], y[rest]))
    return float(np.sqrt(np.mean(np.concatenate(errors) ** 2)))


def cycle_of(series: Series) -> np.ndarray:
    """Each bin's cycle, counted from 0; -1 outside the cycles and where the
    frequency is unusable."""
    period = float(np.median(np.diff(series.load_on)))
    cycle = np.searchsorted(series.load_on, series.at, side="right") - 1
    inside = (cycle >= 0) & (series.at < series.load_on[-1] + period)
    return np.where(inside & usable(series), cycle, -1)


def loaded(series: Series, cycle: np.ndarray) -> np.ndarray:
    """Which bins of the cycles are under load."""
    inside = cycle >= 0
    result = np.zeros(len(cycle), dtype=bool)
    result[inside] = series.at[inside] < series.load_off[cycle[inside]]
    return result


@dataclass
class FastLag:
    """The frequency's curve on the sensor through each of FAST_LAGS."""

    held_out: list[float]  # RMSE, each cycle predicted from the others.
    best_by_fold: list[float]  # Each held-out cycle's own best lag, minutes.
    load_to_rest: list[float]  # Fitted under load, RMSE out of it.
    rest_to_load: list[float]  # The other way.


def fast_lag(series: Series, ambient_minutes: float | None) -> FastLag:
    cycle = cycle_of(series)
    folds = [cycle == i for i in range(len(series.load_on))]
    sensor = fill(series.sensor)
    step = float(np.median(np.diff(series.at)))
    room = (
        None
        if ambient_minutes is None
        else lag(fill(series.ambient), ambient_minutes * SECONDS_PER_MINUTE, step)
    )
    under = loaded(series, cycle)
    rest = (cycle >= 0) & ~under
    # Rows per fold, columns per lag.
    by_fold = np.zeros((len(folds), len(FAST_LAGS)))
    held_out, load_to_rest, rest_to_load = [], [], []
    y = series.ppm
    for j, minutes in enumerate(FAST_LAGS):
        x = columns(lag(sensor, minutes * SECONDS_PER_MINUTE, step), room)
        held_out.append(fold_scores(x, y, folds))
        for i, held in enumerate(folds):
            others = np.logical_or.reduce(folds) & ~held
            by_fold[i, j] = score(
                y[held] - x[held] @ huber_fit(x[others], y[others])
            ).rms
        load_to_rest.append(
            score(y[rest] - x[rest] @ huber_fit(x[under], y[under])).rms
        )
        rest_to_load.append(
            score(y[under] - x[under] @ huber_fit(x[rest], y[rest])).rms
        )
    return FastLag(
        held_out,
        [float(FAST_LAGS[i]) for i in np.argmin(by_fold, 1)],
        load_to_rest,
        rest_to_load,
    )


def describe_score(s: Score) -> str:
    return f"{s.rms:.4f} ({s.bias:+.4f}, {s.spread:.4f})"


def describe_lags(values: list[float]) -> str:
    return " ".join(f"{m:g}:{v:.4f}" for m, v in zip(FAST_LAGS, values))


def report_frozen(
    series: Series, sensor_minutes: float, ambient_minutes: float, only_minutes: float
) -> None:
    """Models with their lags chosen before the cycles, fitted only on what
    was before them, scored on the cycles and after."""
    step = float(np.median(np.diff(series.at)))
    good = usable(series)
    at, y = series.at, series.ppm
    on, off = series.load_on[0], series.load_off[-1]
    # What was fitted, and what is judged.
    before = good & (at < on)
    quiet = before & (at >= series.first_off + SETTLE)
    cycles = good & (at >= on) & (at < off)
    after = good & (at >= off + SETTLE)
    sensor, room = fill(series.sensor), fill(series.ambient)
    print(
        "ranges (sensor, room, degrees C):"
        + "".join(
            f" {name} {np.nanmin(series.sensor[k]):.1f}-{np.nanmax(series.sensor[k]):.1f},"
            f" {np.nanmin(series.ambient[k]):.1f}-{np.nanmax(series.ambient[k]):.1f};"
            for name, k in (
                ("quiet", quiet),
                ("before", before),
                ("cycles", cycles),
                ("after", after),
            )
        )
    )
    outside = (series.sensor < np.nanmin(series.sensor[quiet])) | (
        series.sensor > np.nanmax(series.sensor[quiet])
    )
    print(
        f"sensor outside the quiet fit's range: cycles {outside[cycles].mean():.0%},"
        f" after {outside[after].mean():.0%}"
    )
    print("RMSE, ppm (bias, RMS less bias):")

    def filtered(minutes: float, values: np.ndarray) -> np.ndarray:
        return lag(values, minutes * SECONDS_PER_MINUTE, step)

    models = [
        ("sensor 1 min", columns(filtered(1, sensor))),
        (f"sensor {only_minutes:g} min", columns(filtered(only_minutes, sensor))),
        (
            f"sensor {sensor_minutes:g} + room {ambient_minutes:g} min",
            columns(filtered(sensor_minutes, sensor), filtered(ambient_minutes, room)),
        ),
    ]
    for fitted_name, fitted in (("quiet", quiet), ("before", before)):
        for name, x in models:
            error = y - x @ huber_fit(x[fitted], y[fitted])
            print(
                f"  fit {fitted_name}, {name}: fit {score(error[fitted]).rms:.4f},"
                f" cycles {describe_score(score(error[cycles]))},"
                f" after {describe_score(score(error[after]))}"
            )
    grid: dict[tuple[float, float], tuple[float, float]] = {}
    for s in GRID_SENSOR_LAGS:
        for a in GRID_AMBIENT_LAGS:
            x = columns(filtered(s, sensor), filtered(a, room))
            error = y - x @ huber_fit(x[quiet], y[quiet])
            grid[(s, a)] = (score(error[cycles]).rms, score(error[after]).rms)
    chosen = grid[(sensor_minutes, ambient_minutes)]
    print(
        f"  the frozen one of {len(grid)}, each fitted on quiet:"
        f" cycles rank {1 + sorted(v[0] for v in grid.values()).index(chosen[0])},"
        f" after rank {1 + sorted(v[1] for v in grid.values()).index(chosen[1])}"
    )


def report_fast(series: Series, ambient_minutes: float) -> None:
    for label, room in (
        ("sensor", None),
        (f"+ room {ambient_minutes:g}", ambient_minutes),
    ):
        result = fast_lag(series, room)
        best = FAST_LAGS[int(np.argmin(result.held_out))]
        print(f"each cycle predicted from the others, {label}, by lag in min:")
        print(f"  {describe_lags(result.held_out)}")
        print(f"  best {best:g}; each fold's best {result.best_by_fold}")
        print(f"  load to rest {describe_lags(result.load_to_rest)}")
        print(f"  rest to load {describe_lags(result.rest_to_load)}")


def report_fold(series: Series) -> None:
    cycle = cycle_of(series)
    period = float(np.median(np.diff(series.load_on)))
    print("the cycle, folded: minutes from load on, ppm, degrees C:")
    good = cycle >= 0
    minute = (
        (series.at[good] - series.load_on[cycle[good]]) // SECONDS_PER_MINUTE
    ).astype(int)
    for row in range(0, int(period / SECONDS_PER_MINUTE), FOLD_ROW):
        k = minute == row
        print(
            f"  {row:3d} {np.nanmean(series.ppm[good][k]):+8.3f}"
            f" {np.nanmean(series.sensor[good][k]):6.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    binning = commands.add_parser("bin", help="a recording to a small .npz")
    binning.add_argument("run", help="a pps_raw.py CSV file")
    binning.add_argument("--ambient", required=True, help="the sensor database")
    binning.add_argument("--cycles", required=True, help="the load's on= and off= log")
    binning.add_argument("--first-off", type=float, required=True, help="Unix seconds")
    binning.add_argument("--unusable", nargs="*", default=[], help="UNIX:UNIX spans")
    binning.add_argument("-o", dest="output", required=True)
    reporting = commands.add_parser("report", help="the frozen models and the fast lag")
    reporting.add_argument("series", help="a .npz from bin")
    reporting.add_argument("--sensor-lag", type=float, required=True, help="min")
    reporting.add_argument("--ambient-lag", type=float, required=True, help="min")
    reporting.add_argument("--sensor-only-lag", type=float, required=True, help="min")
    args = parser.parse_args()
    if args.command == "bin":
        make_series(args)
        return
    report_frozen(
        load_series(args.series, COARSE_BIN),
        args.sensor_lag,
        args.ambient_lag,
        args.sensor_only_lag,
    )
    series = load_series(args.series, BIN)
    report_fast(series, args.ambient_lag)
    report_fold(series)


if __name__ == "__main__":
    main()
