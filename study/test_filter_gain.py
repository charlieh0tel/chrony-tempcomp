# SPDX-License-Identifier: GPL-3.0-or-later
"""filter_gain.py, on readings with known answers."""

import numpy as np
import pytest
from filter_gain import (
    Filter,
    blends,
    choose,
    compensation,
    filtered,
    fitted,
    holdover_errors,
    minute_compensation,
    sample,
    steps,
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
    ticks, output = filtered(times, readings, Filter(300.0))
    after = ticks >= 100
    first = np.flatnonzero(after)[0]
    one_tau = np.flatnonzero(ticks >= ticks[first] + 300)[0]
    assert output[first - 1] == 50000
    assert output[one_tau] == pytest.approx(50000 + 1000 * (1 - np.exp(-1)), abs=20)


def test_the_filter_keeps_its_value_through_unread_readings():
    times = np.arange(0.0, 100.0)
    readings = np.full(100, 50000.0)
    readings[40:60] = np.nan
    _, output = filtered(times, readings, Filter(300.0))
    assert not np.isnan(output).any()


def test_unfiltered_compensation_is_the_reading_every_interval():
    times = np.arange(0.0, 120.0)
    readings = 50000.0 + times
    grid = np.array([0.0, 60.0])
    comp = minute_compensation(times, readings, LINE, Filter(0.0), grid)
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
    ticks, crystal = filtered(times, latent, Filter(300.0), period=1.0)
    grid = np.arange(0.0, times[-1], STEP)
    minute = ((ticks - grid[0]) // STEP).astype(int)
    own = -np.bincount(minute, compensation(LINE, crystal)) / np.bincount(minute)

    def error(tau):
        comp = minute_compensation(times, readings, LINE, Filter(tau), grid)
        residual = (own + comp)[10:]
        return np.std(residual)

    assert error(300.0) < 0.5 * error(0.0)


def test_steps_are_the_changes_between_chronyds_readings():
    times = np.arange(0.0, 120.0)
    readings = 50000.0 + np.where(times < 60, 0.0, 1000.0)
    grid = np.array([0.0, 60.0])
    change = steps(times, readings, LINE, Filter(0.0), grid, np.array([True, True]))
    # Readings at 0, 15, ..., 105 s: one step of 0.1 ppm, at 60 s.
    assert sorted(change) == pytest.approx([0.0] * 6 + [0.1])
    only_first = steps(
        times, readings, LINE, Filter(0.0), grid, np.array([True, False])
    )
    assert len(only_first) == 3


def test_the_filter_shrinks_the_steps_of_a_jittery_sensor():
    rng = np.random.default_rng(2)
    times = np.arange(0.0, 3 * 3600.0)
    readings = 55000 + np.round(rng.normal(0, 100, len(times)) / 100) * 100
    grid = np.arange(0.0, times[-1], STEP)
    good = np.ones(len(grid), dtype=bool)

    def noise(tau):
        return np.std(steps(times, readings, LINE, Filter(tau), grid, good))

    assert noise(180.0) < 0.1 * noise(0.0)


def test_the_fit_finds_the_line_and_favors_the_right_time_constant():
    """A crystal 300 s behind three days of moving temperature, read through
    a 0.5 degree step: each time constant's fitted line is near the true
    one, and the fit through 300 s leaves less than through none."""
    rng = np.random.default_rng(3)
    times = np.arange(0.0, 3 * 86400.0)
    latent = 55000 + 3000 * np.sin(2 * np.pi * times / 7200)
    readings = np.round((latent + rng.normal(0, 200, len(times))) / 500) * 500
    ticks, crystal = filtered(times, latent, Filter(300.0), period=1.0)
    grid = np.arange(0.0, times[-1], STEP)
    minute = ((ticks - grid[0]) // STEP).astype(int)
    frequency = -np.bincount(minute, compensation(LINE, crystal)) / np.bincount(minute)
    comps, lines = fitted(
        grid, frequency, [(times, readings)], LINE[0], (Filter(0.0), Filter(300.0))
    )
    assert lines[Filter(300.0)][2] == pytest.approx(LINE[2], rel=0.02)

    def error(tau):
        residual = frequency + comps[Filter(tau)]
        return np.nanstd(residual)

    assert error(300.0) < 0.5 * error(0.0)


def test_a_blend_follows_a_step_by_both_its_lags():
    times = np.arange(0.0, 4000.0)
    readings = np.where(times < 100, 50000.0, 51000.0)
    ticks, output = filtered(times, readings, Filter(60.0, 1200.0, 0.4))
    at = np.flatnonzero(ticks >= 100 + 600)[0]
    elapsed = ticks[at] - ticks[np.flatnonzero(ticks >= 100)[0]]
    fast = 1 - np.exp(-elapsed / 60.0)
    slow = 1 - np.exp(-elapsed / 1200.0)
    assert output[at] == pytest.approx(50000 + 1000 * (0.6 * fast + 0.4 * slow), abs=5)


def test_a_single_lag_is_unchanged_by_the_blend_machinery():
    times = np.arange(0.0, 2000.0)
    readings = np.where(times < 100, 50000.0, 51000.0)
    _, single = filtered(times, readings, Filter(300.0))
    _, blend = filtered(times, readings, Filter(300.0, 900.0, 0.0))
    assert single == pytest.approx(blend)


def test_blends_combine_each_setting_with_the_slow_lag_longer():
    found = blends([60.0, 600.0], [600.0, 1200.0], [0.4])
    assert found == [
        Filter(60.0, 600.0, 0.4),
        Filter(60.0, 1200.0, 0.4),
        Filter(600.0, 1200.0, 0.4),
    ]


def test_choose_takes_each_days_filter_from_the_other_days():
    """Filter a is best on days 0 and 1, b on day 2: day 2 still gets a,
    chosen on days 0 and 1; days 0 and 1 get a too."""
    day = np.repeat([0, 1, 2], 10)
    good = np.ones(30, dtype=bool)
    frequency = np.zeros(30)
    a, b = Filter(60.0), Filter(300.0)
    noise = np.tile([1.0, -1.0], 15)
    comps = {
        a: np.where(day < 2, 0.01, 0.2) * noise,
        b: np.where(day < 2, 0.3, 0.001) * noise,
    }
    comp, chosen = choose(frequency, comps, [a, b], good, day, [0, 1, 2])
    assert chosen == [a, a, a]
    assert comp == pytest.approx(comps[a])
