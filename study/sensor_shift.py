# SPDX-License-Identifier: GPL-3.0-or-later
"""Which comes first, the sensor or the crystal: the crystal's frequency
against its curve on the sensor shifted both ways in time.

A positive shift fits each frequency to the sensor's reading that much
later, as a sensor behind the crystal needs; a negative one, earlier.
Each shift is scored held out: on load cycles (`pps_cycles.py bin`), each
cycle predicted from the others; on recordings (`pps_raw.py`), each whole
day from the others.  Then, through each first-order lag, the best shift
on top of it, and the delay of the two together (a lag's mean delay is
its time constant, so the lag less the shift): positive, the crystal
behind the sensor.

    python3 sensor_shift.py --cycles ../data/draws-cycles.npz
    python3 sensor_shift.py pps-20261106T155224.csv --sensor ... --exclude ...
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import numpy as np
from common import design, huber_fit, lag
from pps_cycles import cycle_of, load_series
from raw_minutes import exclude, place, read_piece, span
from room_days import day_folds

# Bin widths, seconds: the cycles' finest saved and the recordings' grid.
CYCLE_STEP, MINUTE_STEP = 15, 60
# Shifts scanned, in bins either way, and printed every this many bins.
CYCLE_SHIFTS, MINUTE_SHIFTS = 20, 30
CYCLE_PRINT, MINUTE_PRINT = 2, 5
# Lags under the shift, seconds.
CYCLE_TAUS = (0.0, 15.0, 30.0, 45.0, 60.0, 120.0, 180.0, 300.0, 450.0, 600.0)
MINUTE_TAUS = (0.0, 60.0, 180.0, 300.0, 450.0, 600.0, 900.0, 1200.0)
# Fit and held-out masks, one pair per fold.
Folds = list[tuple[np.ndarray, np.ndarray]]


def shifted(values: np.ndarray, bins: int) -> np.ndarray:
    """Each bin given the value `bins` later (earlier if negative), NaN
    where that falls outside."""
    result = np.full(len(values), np.nan)
    if bins >= 0:
        result[: len(values) - bins] = values[bins:]
    else:
        result[-bins:] = values[:bins]
    return result


def filled(values: np.ndarray) -> np.ndarray:
    """Interpolated across NaN, from its mean: the lag needs it throughout."""
    known = ~np.isnan(values)
    result = np.interp(np.arange(len(values)), np.flatnonzero(known), values[known])
    return result - result.mean()


def held_out(x: np.ndarray, y: np.ndarray, folds: Folds) -> float:
    """RMSE of each fold's held-out bins on the fit to its others, over
    bins where both are known."""
    known = ~np.isnan(y) & ~np.isnan(x).any(axis=1)
    errors = []
    for fit, held in folds:
        fit, held = fit & known, held & known
        errors.append(y[held] - x[held] @ huber_fit(x[fit], y[fit]))
    return float(np.sqrt(np.mean(np.concatenate(errors) ** 2)))


def scan(
    sensor: np.ndarray,
    y: np.ndarray,
    folds: Folds,
    step: float,
    tau: float,
    shifts: Sequence[int],
) -> list[float]:
    """Held-out RMSE of the curve on the sensor through `tau`, at each shift."""
    through = lag(sensor, tau, step)
    return [held_out(design(shifted(through, k), True), y, folds) for k in shifts]


def cycle_folds(cycle: np.ndarray) -> Folds:
    folds = [cycle == i for i in range(cycle.max() + 1)]
    inside = cycle >= 0
    return [(inside & ~held, held) for held in folds]


def report(
    sensor: np.ndarray,
    y: np.ndarray,
    folds: Folds,
    step: float,
    reach: int,
    every: int,
    taus: Sequence[float],
) -> None:
    shifts = range(-reach, reach + 1)
    pure = scan(sensor, y, folds, step, 0.0, shifts)
    print("shift s  RMSE ppm   (+ = the sensor's later reading)")
    for k, rms in zip(shifts, pure, strict=True):
        if k % every == 0:
            print(f"{k * step:+7.0f}  {rms:.4f}")
    best = int(np.argmin(pure))
    print(f"best    {shifts[best] * step:+.0f} s, {pure[best]:.4f}")
    print("tau s  best shift s  delay s  RMSE ppm")
    for tau in taus:
        scores = scan(sensor, y, folds, step, tau, shifts)
        best = int(np.argmin(scores))
        shift = shifts[best] * step
        print(f"{tau:5.0f}  {shift:+12.0f}  {tau - shift:+7.0f}  {scores[best]:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*", help="pps_raw.py CSV files")
    parser.add_argument("--cycles", help="pps_cycles.py bin's .npz instead")
    parser.add_argument("--sensor", help="the sensor, for runs")
    parser.add_argument("--exclude", nargs="*", type=span, default=[], help="FROM:TO")
    args = parser.parse_args()
    if args.cycles:
        series = load_series(args.cycles, CYCLE_STEP)
        folds = cycle_folds(cycle_of(series))
        y = np.where(np.logical_or.reduce([h for _, h in folds]), series.ppm, np.nan)
        report(
            filled(series.sensor),
            y,
            folds,
            CYCLE_STEP,
            CYCLE_SHIFTS,
            CYCLE_PRINT,
            CYCLE_TAUS,
        )
        return
    if not args.runs or not args.sensor:
        parser.error("give runs and --sensor, or --cycles")
    arrays = place([read_piece(path, args.sensor) for path in args.runs])
    exclude(arrays, args.exclude)
    y = arrays["frequency"]
    sensor = arrays["temperature"]
    folds = day_folds(arrays["at"], ~np.isnan(y) & ~np.isnan(sensor))
    report(
        filled(sensor), y, folds, MINUTE_STEP, MINUTE_SHIFTS, MINUTE_PRINT, MINUTE_TAUS
    )


if __name__ == "__main__":
    main()
