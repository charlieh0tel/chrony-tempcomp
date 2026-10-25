#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fit a chrony tempcomp point table from chrony's own logs.

Reads tracking.log (frequency) and tempcomp.log (sensor temperature) from a
chrony logdir, rotated .gz files included, and writes a points file for

    tempcomp <sensor> <interval> <points-file>

chrony applies  current_freq = uncomp_freq - comp,  and tracking.log always
records the uncompensated frequency (LCL_ReadAbsoluteFrequency undoes the
compensation), so each point is simply  comp(T) = freq(T) - fref,  and a
refit works the same whether or not tempcomp is already active.

chrony's point-file parser accepts only "<temp> <comp>" lines: a comment or
blank line is fatal.  It interpolates linearly and extrapolates the end
segments, and it ignores any |comp| > 10 ppm.
"""

import argparse
import glob
import gzip
import os
import sys
from datetime import datetime, timezone

import numpy as np

MAX_COMP = 10.0  # ppm, chrony's limit in tempcomp.c


def read_log(logdir, name):
    """Yield (unix time, fields) for each data row of a chrony log and its rotations."""
    paths = glob.glob(os.path.join(logdir, name)) + glob.glob(os.path.join(logdir, name + ".*"))
    if not paths:
        sys.exit(f"no {name} in {logdir}")
    days = {}  # strptime per row is most of the run time on a slow box
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            for line in f:
                if not line[:1].isdigit():
                    continue
                p = line.split()
                if p[0] not in days:
                    d = datetime.strptime(p[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    days[p[0]] = d.timestamp()
                h, m, s = p[1].split(":")
                yield days[p[0]] + int(h) * 3600 + int(m) * 60 + int(s), p


def load(logdir, source, max_skew):
    temp = sorted((t, float(p[2])) for t, p in read_log(logdir, "tempcomp.log"))
    track = sorted((t, float(p[4])) for t, p in read_log(logdir, "tracking.log")
                   if (source == "any" or p[2] == source) and float(p[5]) <= max_skew)
    if len(temp) < 2 or len(track) < 2:
        sys.exit("not enough tempcomp or tracking rows")
    tt, T = np.array(temp).T
    rt, F = np.array(track).T
    return tt, T, rt, F


def pair(tt, T, rt, F, max_gap):
    """Temperature at each tracking time, keeping only times with a sensor sample nearby."""
    i = np.searchsorted(tt, rt).clip(1, len(tt) - 1)
    near = np.minimum(abs(tt[i] - rt), abs(tt[i - 1] - rt)) <= max_gap
    return rt[near], np.interp(rt[near], tt, T), F[near]


def bin_points(Ti, F, step, min_count):
    lo, hi = np.floor(Ti.min() / step) * step, np.ceil(Ti.max() / step) * step
    pts = []
    for c in np.arange(lo, hi + step / 2, step):
        m = (Ti >= c - step / 2) & (Ti < c + step / 2)
        if m.sum() >= min_count:
            pts.append((c, np.median(F[m]), F[m].std(), m.sum()))
    if len(pts) < 2:
        sys.exit("fewer than two populated temperature bins")
    return np.array(pts)


def table(Ti, F, args, fref):
    """Points (temp, comp) including any requested extensions past the data."""
    b = bin_points(Ti, F, args.step, args.min_count)
    pts = [(t, f - fref) for t, f in b[:, :2]]
    if args.extend_low is not None and args.extend_low < pts[0][0]:
        # quadratic through the coolest part of the data; a guess, not a measurement
        m = Ti <= Ti.min() + args.fit_span
        c = np.polyfit(Ti[m], F[m], 2)
        ext = np.arange(args.extend_low, pts[0][0], args.extend_step)
        pts = [(t, np.polyval(c, t) - fref) for t in ext] + pts
    if args.extend_high is not None and args.extend_high > pts[-1][0]:
        pts.append((args.extend_high, pts[-1][1]))
    return np.array(pts), b


def residual(pts, Ti, F, fref):
    return F - fref - np.interp(Ti, pts[:, 0], pts[:, 1])


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logdir", help="chrony logdir (needs tracking.log and tempcomp.log)")
    ap.add_argument("-o", "--output", help="points file to write (default: stdout)")
    ap.add_argument("--source", default="PPS",
                    help="tracking.log reference to trust, by refid or IP, or 'any' (default: PPS)")
    ap.add_argument("--step", type=float, default=500,
                    help="bin width in sensor units (default: 500, i.e. 0.5 C for millidegrees)")
    ap.add_argument("--min-count", type=int, default=30, help="minimum samples per bin (default: 30)")
    ap.add_argument("--max-gap", type=float, default=60,
                    help="seconds from a tracking row to the nearest temperature sample (default: 60)")
    ap.add_argument("--max-skew", type=float, default=0.05,
                    help="drop tracking rows with skew above this, ppm (default: 0.05)")
    ap.add_argument("--fref", type=float,
                    help="frequency that comp=0 maps to, ppm (default: median of the data)")
    ap.add_argument("--extend-low", type=float, metavar="TEMP",
                    help="add points down to TEMP from a quadratic over the coolest data")
    ap.add_argument("--extend-step", type=float, default=5000,
                    help="spacing of --extend-low points (default: 5000)")
    ap.add_argument("--fit-span", type=float, default=10000,
                    help="temperature span of the coolest data --extend-low fits (default: 10000)")
    ap.add_argument("--extend-high", type=float, metavar="TEMP",
                    help="add a point at TEMP holding the hottest bin's value")
    ap.add_argument("--holdout-days", type=float, default=0,
                    help="also fit without the last N days of data and report error on them")
    args = ap.parse_args()

    tt, T, rt, F = load(args.logdir, args.source, args.max_skew)
    rt, Ti, F = pair(tt, T, rt, F, args.max_gap)
    if len(rt) < args.min_count * 2:
        sys.exit(f"only {len(rt)} usable tracking rows from source {args.source}")
    fref = args.fref if args.fref is not None else round(float(np.median(F)), 3)

    log = lambda s="": print(s, file=sys.stderr)
    log(f"data   {utc(rt[0])} .. {utc(rt[-1])} UTC, {len(rt)} rows from {args.source}")
    log(f"temp   {Ti.min():g} .. {Ti.max():g}")
    log(f"freq   {F.min():.3f} .. {F.max():.3f} ppm, sd {F.std():.4f}; fref {fref:+.3f}")

    if args.holdout_days:
        cut = rt[-1] - args.holdout_days * 86400
        train, test = rt < cut, rt >= cut
        if train.sum() < args.min_count * 2 or test.sum() == 0:
            sys.exit("holdout leaves too little data on one side")
        pts, _ = table(Ti[train], F[train], args, fref)
        r = residual(pts, Ti[test], F[test], fref)
        log(f"holdout from {utc(cut)}: {test.sum()} rows, freq sd {F[test].std():.4f} -> "
            f"resid sd {r.std():.4f} ppm, max |resid| {abs(r).max():.3f}")

    pts, bins = table(Ti, F, args, fref)
    r = residual(pts, Ti, F, fref)
    log(f"fit    resid sd {r.std():.4f} ppm over all data")
    noisy = bins[bins[:, 2] > 5 * np.median(bins[:, 2])]
    for t, f, sd, n in noisy:
        log(f"  bin {t:g}: sd {sd:.3f} ppm (n={int(n)}); frequency moves fast here")
    if np.abs(pts[:, 1]).max() > MAX_COMP:
        log(f"WARNING: |comp| exceeds {MAX_COMP} ppm and chrony will ignore it; set --fref closer")

    out = "".join(f"{t:.0f} {c:.4f}\n" for t, c in pts)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        log(f"wrote  {len(pts)} points to {args.output}")
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
