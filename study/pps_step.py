#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The crystal's frequency from `pps_raw.py`'s rows, how it and the
sensors follow CPU load steps, and each sensor's lag.

Usage: python3 pps_step.py RUN.csv [--bin SECONDS] [--chronyd HOST.npz]

The frequency is positive fast, as chronyd's is.  --chronyd compares it
with chronyd's own (frequency less compensation, `fetch.py`'s .npz).
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass

import numpy as np
from common import MAD_SIGMA, cross_validate, design, huber_fit, lag
from pps_raw import COLUMNS, NANOSECONDS

MILLIDEGREES = 1000.0
PPM = 1e6
# Seconds of frequency per bin, by default.
BIN = 60
# A bin with fewer than this share of its edges has no frequency.
MIN_BIN_SHARE = 0.5
# Edges further than this many robust deviations from a bin's line are
# dropped, and deviations are taken as at least this many seconds, so
# clean edges are not picked at.
REJECT = 5.0
MIN_DEVIATION = 1e-8
# Rows over which the realtime clock's rate against the raw is taken.
RATE_ROWS = 64
# A read this long after its edge (ns), or before it, means the realtime
# clock stepped between them or the reader stalled: the row is dropped.
MAX_LATENCY = NANOSECONDS // 2
# Rows the load is median-filtered over, the load above which the CPU
# counts as loaded, and the seconds a state must last to count as a step.
LOAD_ROWS = 15
LOAD_ON = 0.5
MIN_RUN = 300
# Time constants tried for a step, in seconds: geometric, in bins' terms.
STEP_TAUS = np.geomspace(30.0, 4 * 3600.0, 200)
# Lags tried against a whole run, in minutes.
LAGS = [0, 1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60, 90, 120]
# Lags tried cross-predicting heating and cooling, in minutes, and the
# degree of the frequency's curve against the temperature through the lag.
CROSS_LAGS = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 5, 10, 15, 20, 30]
CROSS_DEGREE = 2
# Good bins below which held-out folds are too short to mean anything.
MIN_SCAN_BINS = 60
# chronyd's readings further than this from a bin, in seconds, don't count.
CHRONYD_GAP = 600


@dataclass
class Run:
    """A recording, one array element per edge read."""

    meta: dict[str, str]  # The header's `key=value` lines, except sensors.
    names: dict[str, str]  # Each sensor's path to the kernel's name for it.
    seq: np.ndarray
    real_edge: np.ndarray  # ns, int64, as for the two reads below.
    real_read: np.ndarray
    raw_read: np.ndarray
    pair: np.ndarray  # ns, how far apart the two reads could be.
    busy: np.ndarray  # Fraction of the CPUs' time busy since the last row.
    sensors: dict[str, np.ndarray]  # Path to degrees Celsius, NaN if unread.


@dataclass
class Bins:
    """A run in equal stretches of raw time; NaN where a bin has too few
    edges."""

    t: np.ndarray  # Seconds of raw time at the bin's center, from the start.
    unix: np.ndarray  # The same, in Unix seconds, for matching chronyd's.
    ppm: np.ndarray  # The crystal's frequency, positive fast.
    sensors: dict[str, np.ndarray]  # Path to mean degrees Celsius.
    width: float  # Seconds per bin.
    jitter: float  # Median robust deviation of edges about bins' lines, s.
    dropped: int  # Edges rejected as outliers.


@dataclass
class StepFit:
    """y = y0 + jump + amplitude (1 - exp(-t / tau)) from the step at
    t = 0; y0 before it.  jump is zero when no immediate part is fitted."""

    tau: float  # Seconds.
    tau_low: float  # The range where the fit is within one sigma of best.
    tau_high: float
    y0: float
    jump: float
    amplitude: float
    rms: float


@dataclass
class CrossPrediction:
    """Frequency's curve against the temperature through a lag, fitted on
    one side of the load's end and judged on the other."""

    heating_to_cooling: list[float]  # RMSE, ppm, one per CROSS_LAGS.
    cooling_to_heating: list[float]  # The same, the other way.
    best: int  # Index into CROSS_LAGS, by the worse of the two.
    minimum: float  # Where the best lag's curve, fitted to all, turns.


@dataclass
class LagScan:
    """y against u through a lag, by held-out error."""

    scores: list[float]  # Held-out RMSE, one per LAGS.
    best: int  # Index into LAGS.
    slope: float  # y's slope against u through the best lag.


def load_run(path: str) -> Run:
    """A `pps_raw.py` file.  One clocksource only: another's raw scale is
    another intercept, so the run is to be split there."""
    meta: dict[str, str] = {}
    names: dict[str, str] = {}
    with open(path, newline="") as file:
        lines = iter(file)
        for line in lines:
            if not line.startswith("#"):
                break
            key, value = line[1:].strip().split("=", 1)
            if key == "sensor":
                sensor, _, name = value.partition(" ")
                names[sensor] = name
            else:
                meta[key] = value
        header = next(csv.reader([line]))
        rows = list(csv.reader(lines))
    # A row cut short by the recorder being killed mid-write.
    rows = [row for row in rows if len(row) == len(header)]
    column = {name: [row[i] for row in rows] for i, name in enumerate(header)}
    sources = set(column["clocksource"])
    if len(sources) > 1:
        raise ValueError(f"{path}: more than one clocksource: {sorted(sources)}")

    def integers(name):
        return np.array([int(value) for value in column[name]], dtype=np.int64)

    return Run(
        meta,
        names,
        integers("seq"),
        integers("real_edge_ns"),
        integers("real_read_ns"),
        integers("raw_read_ns"),
        integers("pair_ns"),
        np.array([float(value) for value in column["cpu_busy"]]),
        {
            name: np.array(
                [float(value) / MILLIDEGREES if value else np.nan for value in values]
            )
            for name, values in column.items()
            if name not in COLUMNS
        },
    )


def edge_times(run: Run) -> tuple[np.ndarray, np.ndarray]:
    """(the edges' raw times in seconds from the first read, which rows
    they are): each read, less the time back to its edge at the realtime
    clock's rate against the raw."""
    raw = run.raw_read - run.raw_read[0]
    real = run.real_read - run.real_read[0]
    rows = min(RATE_ROWS, len(raw) - 1)
    rate = np.median((raw[rows:] - raw[:-rows]) / (real[rows:] - real[:-rows]))
    latency = run.real_read - run.real_edge
    good = (latency >= 0) & (latency < MAX_LATENCY)
    return (raw[good] - latency[good] * rate) / NANOSECONDS, np.flatnonzero(good)


def fit_bin(t: np.ndarray) -> tuple[float, float, int]:
    """(ppm, deviation of edges in s, edges dropped) for one bin's edge
    times: the slope of raw time against whole seconds, which is robust
    to edges missing and, unlike each interval's mean, to one late edge."""
    seconds = np.round(t - t[0])
    residual = t - t[0] - seconds
    keep = np.ones(len(t), dtype=bool)
    deviation = 0.0
    for _ in range(2):
        line = np.polyfit(seconds[keep], residual[keep], 1)
        error = residual - np.polyval(line, seconds)
        deviation = MAD_SIGMA * float(np.median(np.abs(error[keep])))
        keep = np.abs(error) <= REJECT * max(deviation, MIN_DEVIATION)
    line = np.polyfit(seconds[keep], residual[keep], 1)
    return float(line[0]) * PPM, deviation, int(np.sum(~keep))


def bin_run(run: Run, width: float = BIN) -> Bins:
    t, rows = edge_times(run)
    count = int(t[-1] // width) + 1
    index = (t // width).astype(int)
    ppm = np.full(count, np.nan)
    unix = np.full(count, np.nan)
    sensors = {name: np.full(count, np.nan) for name in run.sensors}
    deviations = []
    dropped = 0
    origin = run.real_edge[0]
    for i in range(count):
        members = np.flatnonzero(index == i)
        if len(members) < max(MIN_BIN_SHARE * width, 3):
            continue
        ppm[i], deviation, lost = fit_bin(t[members])
        deviations.append(deviation)
        dropped += lost
        # Unix seconds at the bin's center, from its edges' mean.
        real = (run.real_edge[rows[members]] - origin).mean() / NANOSECONDS
        unix[i] = origin / NANOSECONDS + real
        for name, values in run.sensors.items():
            read = values[rows[members]]
            if not np.all(np.isnan(read)):
                sensors[name][i] = np.nanmean(read)
    return Bins(
        (np.arange(count) + 0.5) * width,
        unix,
        ppm,
        sensors,
        width,
        float(np.median(deviations)) if deviations else np.nan,
        dropped,
    )


def find_steps(t: np.ndarray, busy: np.ndarray) -> list[tuple[float, bool]]:
    """(time, whether load rose) of each change in the load that lasts:
    a state shorter than MIN_RUN either side is not a step."""
    if len(busy) < LOAD_ROWS:
        return []
    smooth = np.median(np.lib.stride_tricks.sliding_window_view(busy, LOAD_ROWS), 1)
    centered = t[LOAD_ROWS // 2 :][: len(smooth)]
    loaded = smooth > LOAD_ON
    changes = np.flatnonzero(np.diff(loaded)) + 1
    bounds = np.concatenate([[0], changes, [len(loaded) - 1]])
    return [
        (float(centered[change]), bool(loaded[change]))
        for i, change in enumerate(changes)
        if centered[change] - centered[bounds[i]] >= MIN_RUN
        and centered[bounds[i + 2]] - centered[change] >= MIN_RUN
    ]


def fit_step(
    t: np.ndarray, y: np.ndarray, start: float, immediate: bool
) -> StepFit | None:
    """The first-order response best fitting y(t) around a step at
    `start`, or None without readings on both sides."""
    known = ~np.isnan(y)
    x, y = t[known] - start, y[known]
    after = x >= 0
    if not after.any() or after.all():
        return None
    columns = 3 if immediate else 2
    if len(x) <= columns + 1:
        return None
    errors, fits = [], []
    for tau in STEP_TAUS:
        rise = np.where(after, 1 - np.exp(-x / tau), 0.0)
        parts = [np.ones_like(x)] + ([after.astype(float)] if immediate else [])
        X = np.column_stack(parts + [rise])
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        fits.append(beta)
        errors.append(float(np.sum((y - X @ beta) ** 2)))
    sse = np.array(errors)
    best = int(np.argmin(sse))
    variance = sse[best] / (len(x) - columns - 1)
    within = STEP_TAUS[sse <= sse[best] + variance]
    beta = fits[best]
    return StepFit(
        float(STEP_TAUS[best]),
        float(within[0]),
        float(within[-1]),
        float(beta[0]),
        float(beta[1]) if immediate else 0.0,
        float(beta[-1]),
        float(np.sqrt(sse[best] / len(x))),
    )


def scan_lags(u: np.ndarray, y: np.ndarray, width: float) -> LagScan | None:
    """y's fit against u through each of LAGS.  u's gaps are interpolated,
    as the filter needs it throughout.  None if too little or u is flat."""
    known = ~np.isnan(u)
    good = known & ~np.isnan(y)
    if good.sum() < MIN_SCAN_BINS or np.ptp(u[good]) == 0:
        return None
    filled = np.interp(np.arange(len(u)), np.flatnonzero(known), u[known])
    filled = filled - filled[good].mean()
    scores = []
    for minutes in LAGS:
        x = design(lag(filled, minutes * 60, width), False)
        scores.append(
            cross_validate(
                lambda train, x=x: x @ huber_fit(x[train], y[train]), good, y
            )
        )
    best = int(np.argmin(scores))
    x = design(lag(filled, LAGS[best] * 60, width), False)
    return LagScan(scores, best, float(huber_fit(x[good], y[good])[1]))


def curve_error(
    x: np.ndarray, y: np.ndarray, fitted: np.ndarray, judged: np.ndarray
) -> float:
    """RMSE on the samples `judged` of y's curve on x fitted on `fitted`."""
    curve = np.polyfit(x[fitted], y[fitted], CROSS_DEGREE)
    return float(np.sqrt(np.mean((y[judged] - np.polyval(curve, x[judged])) ** 2)))


def cross_predict(
    t: np.ndarray, u: np.ndarray, y: np.ndarray, load_off: float, width: float
) -> CrossPrediction | None:
    """Fit y on u through each lag before the load ends and predict after,
    and the other way.  Both sides cross the same temperatures, so this
    interpolates, where a held-out fold of a step run would extrapolate:
    a whole fold of it can be hotter than all the rest."""
    known = ~np.isnan(u)
    good = known & ~np.isnan(y)
    heating, cooling = good & (t < load_off), good & (t >= load_off)
    if heating.sum() <= CROSS_DEGREE or cooling.sum() <= CROSS_DEGREE:
        return None
    filled = np.interp(np.arange(len(u)), np.flatnonzero(known), u[known])
    filtered = [lag(filled, minutes * 60, width) for minutes in CROSS_LAGS]
    forward = [curve_error(x, y, heating, cooling) for x in filtered]
    backward = [curve_error(x, y, cooling, heating) for x in filtered]
    best = int(np.argmin(np.maximum(forward, backward)))
    curve = np.polyfit(filtered[best][good], y[good], CROSS_DEGREE)
    return CrossPrediction(forward, backward, best, float(-curve[1] / (2 * curve[0])))


def chronyd_own(path: str, unix: np.ndarray) -> np.ndarray:
    """chronyd's frequency less its compensation, at the times `unix`, NaN
    where it has no reading near."""
    data = np.load(path)
    own = data["frequency"] - data["compensation"]
    known = ~np.isnan(own)
    at, own = data["at"][known], own[known]
    result = np.interp(unix, at, own)
    near = np.clip(np.searchsorted(at, unix), 1, len(at) - 1)
    far = np.minimum(np.abs(unix - at[near - 1]), np.abs(at[near] - unix)) > CHRONYD_GAP
    result[far | np.isnan(unix)] = np.nan
    return result


def describe_step(name: str, fit: StepFit | None, unit: str) -> str:
    if fit is None:
        return f"  {name}: no fit"
    return (
        f"  {name}: tau {fit.tau / 60:.1f} min ({fit.tau_low / 60:.1f}"
        f" to {fit.tau_high / 60:.1f}), from {fit.y0:+.3f} jump {fit.jump:+.3f}"
        f" then {fit.amplitude:+.3f} {unit}, rms {fit.rms:.3f}"
    )


def describe_scan(name: str, scan: LagScan | None, slope_unit: str) -> str:
    if scan is None:
        return f"  {name}: too little data or no change"
    return (
        f"  {name}: best {LAGS[scan.best]} min, held-out RMSE"
        f" {scan.scores[scan.best]:.4f} ppm against {scan.scores[0]:.4f} with none,"
        f" slope {scan.slope:+.4f} {slope_unit}"
    )


def report(run: Run, bins: Bins, npz: str | None) -> None:
    good = ~np.isnan(bins.ppm)
    edges = len(run.seq)
    lost = int(run.seq[-1] - run.seq[0] + 1 - edges)
    print(f"{run.meta.get('host', '?')} boot {run.meta.get('boot_id', '?')}")
    print(
        f"{edges} edges over {bins.t[-1] / 3600:.2f} h, {lost} not read,"
        f" {bins.dropped} dropped; edge deviation {bins.jitter * 1e9:.0f} ns,"
        f" median clock pair {np.median(run.pair):.0f} ns,"
        f" median read {np.median(run.real_read - run.real_edge) / 1e6:.1f} ms"
    )
    print(
        f"frequency {np.nanmin(bins.ppm):+.3f} to {np.nanmax(bins.ppm):+.3f} ppm,"
        f" {good.sum()} of {len(good)} bins of {bins.width:.0f} s"
    )

    raw_times = (run.raw_read - run.raw_read[0]) / NANOSECONDS
    steps = find_steps(raw_times, run.busy)
    print(f"{len(steps)} load steps")
    bounds = [0.0] + [start for start, _ in steps] + [float(bins.t[-1]) + bins.width]
    for i, (start, rose) in enumerate(steps):
        # From the step before to the step after: what is between is this one's.
        inside = (bins.t >= bounds[i]) & (bins.t < bounds[i + 2])
        t = bins.t[inside]
        print(f"load {'on' if rose else 'off'} at {start / 60:.1f} min:")
        for immediate in (False, True):
            label = "frequency" + (", with jump" if immediate else "")
            fit = fit_step(t, bins.ppm[inside], start, immediate)
            print(describe_step(label, fit, "ppm"))
        for name, values in bins.sensors.items():
            fit = fit_step(t, values[inside], start, False)
            label = f"{name} ({run.names.get(name, '?')})"
            print(describe_step(label, fit, "degrees"))

    load_offs = [start for start, rose in steps if not rose]
    if load_offs:
        print(
            "frequency's curve on each sensor through a lag, fitted on one"
            " side of the load's end, RMSE on the other (ppm), by lag in min:"
        )
        for name, values in bins.sensors.items():
            print(f"  {name} ({run.names.get(name, '?')}):")
            cross = cross_predict(bins.t, values, bins.ppm, load_offs[0], bins.width)
            if cross is None:
                print("    too little data")
                continue
            for minutes, forward, backward in zip(
                CROSS_LAGS, cross.heating_to_cooling, cross.cooling_to_heating
            ):
                print(f"    {minutes:5.2f}  {forward:.3f}  {backward:.3f}")
            print(
                f"    best {CROSS_LAGS[cross.best]} min, curve turns at"
                f" {cross.minimum:.1f} degrees C"
            )

    if npz:
        own = chronyd_own(npz, bins.unix)
        both = good & ~np.isnan(own)
        if not both.any():
            print(f"{npz}: no readings in this run")
            return
        difference = own[both] - bins.ppm[both]
        print(
            f"chronyd's own less the raw frequency: median"
            f" {np.median(difference):+.3f} ppm (this boot's scale),"
            f" spread {np.std(difference):.3f} ppm"
        )
        print("chronyd's own against the raw frequency through a lag:")
        print(
            describe_scan(
                "raw frequency", scan_lags(bins.ppm, own, bins.width), "ppm/ppm"
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="a pps_raw.py CSV file")
    parser.add_argument("--bin", type=float, default=BIN, help=f"seconds ({BIN})")
    parser.add_argument("--chronyd", help="the host's fetch.py .npz, to compare")
    args = parser.parse_args()
    run = load_run(args.run)
    report(run, bin_run(run, args.bin), args.chronyd)


if __name__ == "__main__":
    main()
