# SPDX-License-Identifier: GPL-3.0-or-later
"""room_days.py, on simulated days with known answers."""

import numpy as np
import pytest
import room_days
from common import design, lag

DAYS = 6
SEED = 1
NOISE = 1e-3


def simulate_days(room_ppm_per_degree):
    """A sensor and a room of different periods, so they can be told apart;
    the crystal 2 min behind the sensor, and the room as given."""
    at = np.arange(0.0, DAYS * room_days.SECONDS_PER_DAY, room_days.STEP)
    sensor = 3 * np.sin(2 * np.pi * at / 5000.0)
    room = 2 * np.sin(2 * np.pi * at / 70000.0)
    rng = np.random.default_rng(SEED)
    y = (
        -0.1 * lag(sensor, 120.0, room_days.STEP)
        + room_ppm_per_degree * room
        + NOISE * rng.standard_normal(len(at))
    )
    return at, sensor, room, y


def test_day_folds_hold_each_day_out_with_a_margin():
    at = np.arange(0.0, 3 * room_days.SECONDS_PER_DAY, room_days.STEP)
    folds = room_days.day_folds(at, np.ones(len(at), dtype=bool), margin=10)
    assert len(folds) == 3
    fitted, held = folds[1]
    first = np.flatnonzero(held)[0]
    last = np.flatnonzero(held)[-1]
    assert not fitted[first - 10 : last + 11].any()
    assert fitted[first - 11] and fitted[last + 11]


def test_day_folds_skip_a_short_day():
    at = np.arange(0.0, 2 * room_days.SECONDS_PER_DAY, room_days.STEP)
    good = np.ones(len(at), dtype=bool)
    good[: room_days.SECONDS_PER_DAY // room_days.STEP - 1] = False
    assert len(room_days.day_folds(at, good)) == 1


def test_day_folds_of_an_empty_span_are_none():
    assert room_days.day_folds(np.empty(0), np.empty(0, dtype=bool)) == []


def test_trimmed_fit_ignores_an_outlier():
    x = design(np.arange(100.0), False)
    y = 2.0 + 0.5 * np.arange(100.0) + 1e-3 * np.sin(np.arange(100.0))
    y[50] += 1000.0
    assert room_days.trimmed_fit(x, y) == pytest.approx([2.0, 0.5], abs=1e-2)


def test_nested_never_chooses_a_day_by_itself():
    good, bad = np.array([0.1]), np.array([1.0])
    # "a" wins only on day 0: held out, day 0 must take "b", best on the rest.
    table = {"a": [np.array([0.0]), 2 * bad, 2 * bad], "b": [bad, good, good]}
    errors, chosen = room_days.nested(table)
    assert chosen == ["b", "b", "b"]
    assert errors[0] == bad


def test_a_room_that_matters_is_found_on_held_out_days():
    at, sensor, room, y = simulate_days(-0.05)
    folds = room_days.day_folds(at, np.ones(len(at), dtype=bool))
    x = design(lag(sensor, 120.0, room_days.STEP), True)
    without = room_days.held_out_errors(x, y, folds)
    within = room_days.held_out_errors(np.column_stack([x, room]), y, folds)
    assert room_days.rms(np.concatenate(within)) < 0.1 * room_days.rms(
        np.concatenate(without)
    )


def test_a_room_that_does_not_matter_gains_nothing():
    at, sensor, room, y = simulate_days(0.0)
    folds = room_days.day_folds(at, np.ones(len(at), dtype=bool))
    x = design(lag(sensor, 120.0, room_days.STEP), True)
    without = room_days.rms(np.concatenate(room_days.held_out_errors(x, y, folds)))
    within = room_days.rms(
        np.concatenate(room_days.held_out_errors(np.column_stack([x, room]), y, folds))
    )
    assert within == pytest.approx(without, rel=0.01)


def test_compensation_spans_leave_out_the_day_it_came_on():
    day = room_days.SECONDS_PER_DAY
    at = np.arange(0.0, 4 * day, room_days.STEP)
    compensation = np.where(at >= 1.5 * day, 0.1, 0.0)
    compensation[0] = np.nan
    spans = room_days.compensation_spans(at, compensation)
    assert at[spans["compensation off"]].max() < day
    assert at[spans["compensation on"]].min() >= 2 * day


def test_compensation_spans_with_none_keep_everything():
    at = np.arange(0.0, 86400.0, room_days.STEP)
    spans = room_days.compensation_spans(at, np.zeros(len(at)))
    assert list(spans) == ["compensation off"]
    assert spans["compensation off"].all()


def test_host_name_is_the_file_without_its_directory():
    assert room_days.host_name("../data/apu-0.npz") == "apu-0"
