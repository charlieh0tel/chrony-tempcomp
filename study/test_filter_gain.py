# SPDX-License-Identifier: GPL-3.0-or-later
"""filter_gain.py, on readings with known answers."""

import numpy as np
import pytest
from filter_gain import (
    compensation,
    filtered,
    holdover_errors,
    minute_compensation,
    sample,
)
from room_days import STEP

LINE = (50000.0, 0.0, 1e-4, 0.0)


def test_compensation_is_chronys_formula():
    line = (50000.0, 0.5, 1e-4, -1e-8)
    assert compensation(line, np.array([51000.0]))[0] == pytest.approx(
        0.5 + 1000 * 1e-4 - 1e6 * 1e-8
    )


def test_sample_takes_the_last_value_at_or_before():
    times = np.array([0.0, 1.0, 2.0])
    values = np.array([10.0, 11.0, 12.0])
    assert list(sample(times, values, np.array([0.5, 2.0, 5.0]))) == [10, 12, 12]
    assert np.isnan(sample(times, values, np.array([-1.0]))[0])


def test_the_filter_follows_a_step_by_its_time_constant():
    times = np.arange(0.0, 2000.0)
    readings = np.where(times < 100, 50000.0, 51000.0)
    ticks, output = filtered(times, readings, 300.0)
    after = ticks >= 100
    first = np.flatnonzero(after)[0]
    one_tau = np.flatnonzero(ticks >= ticks[first] + 300)[0]
    assert output[first - 1] == 50000
    assert output[one_tau] == pytest.approx(50000 + 1000 * (1 - np.exp(-1)), abs=20)


def test_the_filter_keeps_its_value_through_unread_readings():
    times = np.arange(0.0, 100.0)
    readings = np.full(100, 50000.0)
    readings[40:60] = np.nan
    _, output = filtered(times, readings, 300.0)
    assert not np.isnan(output).any()


def test_unfiltered_compensation_is_the_reading_every_interval():
    times = np.arange(0.0, 120.0)
    readings = 50000.0 + times
    grid = np.array([0.0, 60.0])
    comp = minute_compensation(times, readings, LINE, 0.0, grid)
    # Readings at 0, 15, 30 and 45 s, then 60 to 105 s.
    assert comp == pytest.approx([1e-4 * 22.5, 1e-4 * 82.5])


def test_holdover_error_is_zero_for_a_constant_error():
    assert np.allclose(holdover_errors(np.full(600, 0.03), 1), 0.0)


def test_holdover_error_integrates_a_change_after_the_loss():
    error = np.zeros(600)
    error[300:] = 0.01
    held = holdover_errors(error, 1)
    # Starts are hourly from minute 15: the one at 255 holds 15 minutes at
    # 0.01 ppm past the change, 9 us; the next has the change in its estimate.
    assert held.max() == pytest.approx(0.01 * 15 * STEP)
    assert sorted(held)[:-1] == pytest.approx([0.0] * (len(held) - 1))


def test_holdover_skips_spans_mostly_unknown():
    error = np.full(600, np.nan)
    assert len(holdover_errors(error, 1)) == 0


def test_the_right_time_constant_beats_the_unfiltered_sensor():
    """A crystal 300 s behind a slowly moving temperature, read through a
    0.5 degree step: the filter at 300 s leaves less than none."""
    rng = np.random.default_rng(1)
    times = np.arange(0.0, 6 * 3600.0)
    latent = 55000 + 3000 * np.sin(2 * np.pi * times / 7200)
    readings = np.round((latent + rng.normal(0, 200, len(times))) / 500) * 500
    ticks, crystal = filtered(times, latent, 300.0, period=1.0)
    grid = np.arange(0.0, times[-1], STEP)
    minute = ((ticks - grid[0]) // STEP).astype(int)
    own = -np.bincount(minute, compensation(LINE, crystal)) / np.bincount(minute)

    def error(tau):
        comp = minute_compensation(times, readings, LINE, tau, grid)
        residual = (own + comp)[10:]
        return np.std(residual)

    assert error(300.0) < 0.5 * error(0.0)
