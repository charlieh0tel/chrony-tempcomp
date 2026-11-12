# SPDX-License-Identifier: GPL-3.0-or-later
"""sensor_shift.py, on crystals of known delay behind or ahead of the sensor."""

import numpy as np
import pytest
import sensor_shift

BINS, FOLDS, DELAY = 4000, 4, 3
STEP = 15.0
SEED = 2
NOISE = 1e-3


def sensor_and_folds():
    t = np.arange(BINS)
    sensor = np.sin(2 * np.pi * t / 400.0) + 0.5 * np.sin(2 * np.pi * t / 97.0)
    held = [(t * FOLDS // BINS) == i for i in range(FOLDS)]
    return sensor, [(~h, h) for h in held]


def crystal(sensor, bins):
    """A curve on the sensor's reading `bins` later (earlier if negative)."""
    rng = np.random.default_rng(SEED)
    x = sensor_shift.shifted(sensor, bins)
    return 0.3 * x - 0.05 * x * x + NOISE * rng.standard_normal(len(x))


def test_shifted_moves_values_and_pads_with_nan():
    values = np.arange(5.0)
    assert sensor_shift.shifted(values, 2)[:3].tolist() == [2.0, 3.0, 4.0]
    assert np.isnan(sensor_shift.shifted(values, 2)[3:]).all()
    assert sensor_shift.shifted(values, -1)[1:].tolist() == [0.0, 1.0, 2.0, 3.0]
    assert np.isnan(sensor_shift.shifted(values, -1)[0])


@pytest.mark.parametrize("delay", [DELAY, -DELAY])
def test_the_scan_finds_the_crystals_side_of_the_sensor(delay):
    sensor, folds = sensor_and_folds()
    y = crystal(sensor, -delay)
    shifts = range(-2 * DELAY, 2 * DELAY + 1)
    scores = sensor_shift.scan(sensor, y, folds, STEP, 0.0, shifts)
    assert shifts[int(np.argmin(scores))] == -delay
    assert min(scores) == pytest.approx(NOISE, rel=0.2)


def test_cycle_folds_hold_each_cycle_out_and_leave_out_the_rest():
    cycle = np.array([-1, 0, 0, 1, 1, -1])
    folds = sensor_shift.cycle_folds(cycle)
    assert len(folds) == 2
    fit, held = folds[0]
    assert held.tolist() == [False, True, True, False, False, False]
    assert fit.tolist() == [False, False, False, True, True, False]
