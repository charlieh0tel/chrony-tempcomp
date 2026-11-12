# SPDX-License-Identifier: GPL-3.0-or-later
"""chronyd_step.py, on logs written with a known response."""

import datetime
import gzip
import lzma

import chronyd_step
import pytest

START = datetime.datetime(2026, 11, 10, 23, 50, tzinfo=datetime.UTC)
# The step's time after START, seconds, its size and the loop's delay.
STEP_AT, STEP_PPM, DELAY = 600, -0.886, 96
UPDATE, SAMPLES = 16, 11


def stamp(seconds):
    return f"{START + datetime.timedelta(seconds=seconds):%Y-%m-%d %H:%M:%S}"


def write_logs(directory):
    """Logs over 30 min: the compensation steps at STEP_AT, the loop's
    frequency follows DELAY later, with a peak offset of 20 us; the
    statistics are split across three files, plain, gzipped and xz'd."""
    span = range(0, 1800, UPDATE)
    (directory / "tempcomp.log").write_text(
        "".join(
            f"{stamp(t)}  5.0e+04  {(0.886 if t < STEP_AT else 0.0):.4e}\n"
            for t in range(0, 1800, 15)
        )
    )
    tracking = ["   Date (UTC) Time     IP Address   St   Freq ppm\n"]
    for t in span:
        moved = t >= STEP_AT + DELAY
        offset = 20e-6 if STEP_AT + DELAY - UPDATE <= t < STEP_AT + DELAY else 1e-7
        tracking.append(
            f"{stamp(t)} GPS 1 {4.389 + (STEP_PPM if moved else 0.0):.3f} "
            f"0.003 {offset:.3e} N 1\n"
        )
        tracking.append(f"{stamp(t)} NMEA 1 9.999 0.003 1e-3 N 1\n")
    (directory / "tracking.log").write_text("".join(tracking))
    statistics = [
        f"{stamp(t)} GPS 3e-07 -3e-07 2e-07 -9e-09 4e-08 1.3e+00 {SAMPLES} 0 6 0.00\n"
        for t in span
    ]
    third = len(statistics) // 3
    with lzma.open(directory / "statistics.log.2.xz", "wt") as oldest:
        oldest.write("".join(statistics[:third]))
    with gzip.open(directory / "statistics.log.1.gz", "wt") as old:
        old.write("".join(statistics[third : 2 * third]))
    (directory / "statistics.log").write_text("".join(statistics[2 * third :]))


def test_the_step_and_the_loops_response_are_found(tmp_path):
    write_logs(tmp_path)
    compensation = chronyd_step.table(
        tmp_path, "tempcomp", (chronyd_step.TEMPCOMP_COMPENSATION,)
    )
    found = chronyd_step.steps(compensation, 0.1)
    assert len(found) == 1
    at, size = found[0]
    assert size == pytest.approx(STEP_PPM)
    tracking = chronyd_step.table(
        tmp_path,
        "tracking",
        (chronyd_step.TRACKING_FREQUENCY, chronyd_step.TRACKING_OFFSET),
        "GPS",
        chronyd_step.TRACKING_SOURCE,
    )
    r = chronyd_step.response(tracking, at)
    assert r["moved"] == pytest.approx(STEP_PPM)
    # The frequency moves at the first update DELAY after the step.
    first = (STEP_AT + DELAY + UPDATE - 1) // UPDATE * UPDATE
    assert r["10%"] == r["90%"] == first - (at - START.timestamp())
    assert r["offset"] == pytest.approx(20e-6)


def test_the_window_comes_from_the_statistics_across_rotations(tmp_path):
    write_logs(tmp_path)
    statistics = chronyd_step.table(
        tmp_path,
        "statistics",
        (chronyd_step.STATISTICS_SAMPLES,),
        "GPS",
        chronyd_step.STATISTICS_SOURCE,
    )
    assert len(statistics) == len(range(0, 1800, UPDATE))
    w = chronyd_step.window(statistics)
    assert w["update"] == UPDATE
    assert w["samples"] == SAMPLES
    assert w["window"] == UPDATE * SAMPLES


def test_small_changes_are_not_steps():
    compensation = chronyd_step.np.array([[0.0, 0.886], [15.0, 0.887], [30.0, 0.0]])
    assert chronyd_step.steps(compensation, 0.1) == [(30.0, pytest.approx(-0.887))]


def test_raw_readings_flipping_are_not_steps():
    t = chronyd_step.np.arange(0.0, 1800.0, 15.0)
    flips = 0.5 + 0.123 * (t // 60 % 2)
    compensation = chronyd_step.np.column_stack([t, flips])
    assert chronyd_step.steps(compensation, 0.1) == []
