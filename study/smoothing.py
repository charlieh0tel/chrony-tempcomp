#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Is a fitted lag a delay, only smoothing of a noisy sensor, or a second,
slower path?

The readings' one-minute buckets, not lag.py's five: a lag of a minute or
two must not be averaged away.  Three tests of the frequency's curve on
the temperature, by held-out error:

- Filters: a causal first-order lag, which smooths and delays; the same
  run forward and backward, which smooths as much with no delay; a pure
  delay, which delays with no smoothing.
- A control: a simulated crystal behind the slowly moving temperature by
  a known lag, read through the sensor's step and noise, for what the
  same filters report for it.
- Two paths: a share through a fast lag, the rest through a slow one.
  Given a pps_raw.py run with a load step, the same shares are judged on
  it, fitted on one side of the load's end and predicting the other.

Usage: python3 smoothing.py [--step DEGREES] [--run RUN.csv] HOST.npz
"""

import argparse

import numpy as np
import pps_step
from common import cross_validate, design, huber_fit, lag
from scipy.ndimage import uniform_filter1d
from scipy.signal import lfilter

# Seconds per bucket, as `fetch.py` asks, and the buckets either side of a
# held-out fold also kept out of its fit: an hour, as lag.py's folds have.
BUCKET = 60.0
MARGIN = 60
# Time constants tried, in minutes.
TAUS = [0, 1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60]
# Minutes the temperature is averaged over to stand for what really moves,
# in the control: readings about this leave as much noise as a quiet
# stretch of a 1 Hz recording does (0.18 and 0.17 degrees C on draws).
LATENT_WINDOW = 11
# Lags the control's crystal is given, in minutes, and its simulations of
# each, every one with its own noise.
CONTROL_LAGS = [0, 1, 3, 5, 7, 10]
RUNS = 4
SEED = 1
# Minutes through the two paths' fast lag and slow lags, and the fast share.
FAST_LAG = 1
SLOW_LAGS = [3, 5, 10, 20, 30, 60]
FAST_SHARES = [1.0, 0.9, 0.8, 0.6, 0.4, 0.2]
# Seconds per bin of the step run.
RUN_BIN = 15.0


def forward_backward(u, tau):
    """u through a first-order lag each way: the same smoothing, no delay."""
    if tau <= 0:
        return u
    return lag(lag(u, tau, BUCKET)[::-1], tau, BUCKET)[::-1]


def delayed(u, tau):
    """u, `tau` seconds late."""
    samples = round(tau / BUCKET)
    return np.concatenate([np.full(samples, u[0]), u[: len(u) - samples]])


def two_paths(u, share, slow, step):
    """u, `share` of it through the fast lag and the rest through `slow`
    minutes."""
    return share * lag(u, FAST_LAG * 60, step) + (1 - share) * lag(u, slow * 60, step)


def score(x, y, good):
    """Held-out RMSE of y's curve on x, ppm."""
    X = design(x - x[good].mean(), True)
    return cross_validate(
        lambda train: X @ huber_fit(X[train], y[train]), good, y, MARGIN
    )


def filled(values):
    known = ~np.isnan(values)
    return np.interp(np.arange(len(values)), np.flatnonzero(known), values[known])


def read_host(path):
    """(temperature in degrees C with its gaps filled, the clock's own
    frequency, which buckets have both)."""
    data = np.load(path)
    u = data["temperature"] / 1000.0
    y = data["frequency"] - data["compensation"]
    return filled(u), y, ~np.isnan(u) & ~np.isnan(y)


def control(u, y, good, step, true_lag, rng):
    """What each filter reports for a crystal `true_lag` minutes behind the
    host's slow temperature, with the host's own curve and noise: (causal,
    forward-backward) mean held-out RMSE per TAUS."""
    latent = uniform_filter1d(u, LATENT_WINDOW, mode="nearest")
    # The reading's noise beyond what its step accounts for.
    extra = max(np.var(u[good] - latent[good]) - step**2 / 12, 0.0) ** 0.5
    X = design(lag(u - u[good].mean(), true_lag * 60, BUCKET), True)
    beta = huber_fit(X[good], y[good])
    residual = y[good] - X[good] @ beta
    # The frequency's own noise stays correlated, as an AR(1).
    rho = float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
    innovation = np.std(residual) * np.sqrt(1 - rho**2)
    causal, smooth = np.zeros(len(TAUS)), np.zeros(len(TAUS))
    crystal = lag(latent - latent[good].mean(), true_lag * 60, BUCKET)
    for _ in range(RUNS):
        noise = lfilter([1], [1, -rho], rng.normal(0, innovation, len(u)))
        truth = design(crystal, True) @ beta + noise
        reading = np.round((latent + rng.normal(0, extra, len(u))) / step) * step
        for i, tau in enumerate(TAUS):
            causal[i] += score(lag(reading, tau * 60, BUCKET), truth, good) / RUNS
            smooth[i] += score(forward_backward(reading, tau * 60), truth, good) / RUNS
    return causal, smooth


def judge_on_run(path):
    """Print each pair of paths' error across a run's load end."""
    run = pps_step.load_run(path)
    bins = pps_step.bin_run(run, RUN_BIN)
    raw_times = (run.raw_read - run.raw_read[0]) / pps_step.NANOSECONDS
    load_off = next(
        start for start, rose in pps_step.find_steps(raw_times, run.busy) if not rose
    )
    name = next(iter(bins.sensors))
    u, y = bins.sensors[name], bins.ppm
    good = ~np.isnan(u) & ~np.isnan(y)
    heating, cooling = good & (bins.t < load_off), good & (bins.t >= load_off)
    u = filled(u)
    print(f"the same on {path}, heating to cooling / cooling to heating, ppm:")
    print(" slow  " + "".join(f"{share:^12.1f}" for share in FAST_SHARES))
    for slow in SLOW_LAGS:
        cells = []
        for share in FAST_SHARES:
            x = two_paths(u, share, slow, RUN_BIN)
            forward = pps_step.curve_error(x, y, heating, cooling)
            backward = pps_step.curve_error(x, y, cooling, heating)
            cells.append(f"{forward:.3f}/{backward:.3f} ")
        print(f"{slow:5d}  " + "".join(cells))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--step", type=float, default=0.538, help="sensor's step, C")
    parser.add_argument("--run", help="a pps_raw.py run with a load step")
    parser.add_argument("host")
    args = parser.parse_args()
    u, y, good = read_host(args.host)
    print(f"{args.host}: {good.sum() / 1440:.1f} days of one-minute buckets")
    print("held-out RMSE, ppm, by time constant in min")
    print(" tau   causal  fwd-back  delay")
    for tau in TAUS:
        seconds = tau * 60
        print(
            f"{tau:4d}  {score(lag(u, seconds, BUCKET), y, good):.4f}"
            f"  {score(forward_backward(u, seconds), y, good):.4f}"
            f"    {score(delayed(u, seconds), y, good):.4f}"
        )
    print(f"control, step {args.step} C, by the crystal's true lag in min:")
    print(" true   none   causal best  fwd-back best")
    rng = np.random.default_rng(SEED)
    for true_lag in CONTROL_LAGS:
        causal, smooth = control(u, y, good, args.step, true_lag, rng)
        print(
            f"{true_lag:5d}  {causal[0]:.4f}"
            f"  {causal.min():.4f} at {TAUS[int(np.argmin(causal))]:2d}"
            f"  {smooth.min():.4f} at {TAUS[int(np.argmin(smooth))]:2d}"
        )
    print(f"two paths, {FAST_LAG} min and slow, by the fast share, ppm:")
    print(" slow  " + "".join(f"{share:^8.1f}" for share in FAST_SHARES))
    for slow in SLOW_LAGS:
        cells = [
            f"{score(two_paths(u, share, slow, BUCKET), y, good):.4f}  "
            for share in FAST_SHARES
        ]
        print(f"{slow:5d}  " + "".join(cells))
    if args.run:
        judge_on_run(args.run)


if __name__ == "__main__":
    main()
