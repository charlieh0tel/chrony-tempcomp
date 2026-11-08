# SPDX-License-Identifier: GPL-3.0-or-later
"""raw_minutes.py, on pieces with known answers."""

import numpy as np
import raw_minutes
import room_days


def piece(start, minutes, ppm, degrees):
    at = start + room_days.STEP * (np.arange(minutes) + 0.5)
    return at, np.full(minutes, ppm), np.full(minutes, degrees)


def test_place_lays_files_on_one_grid_with_a_gap_between():
    arrays = raw_minutes.place([piece(600.0, 3, 1.0, 40.0), piece(900.0, 2, 2.0, 41.0)])
    assert list(arrays["at"]) == [600.0, 660.0, 720.0, 780.0, 840.0, 900.0, 960.0]
    frequency = arrays["frequency"]
    assert list(frequency[:3]) == [1.0, 1.0, 1.0]
    assert np.isnan(frequency[3:5]).all()
    assert list(frequency[5:]) == [2.0, 2.0]
    assert arrays["temperature"][0] == 40000.0
    assert not arrays["compensation"].any()


def test_place_starts_on_a_whole_minute():
    arrays = raw_minutes.place([piece(615.0, 2, 1.0, 40.0)])
    assert list(arrays["at"]) == [600.0, 660.0]
    assert not np.isnan(arrays["frequency"]).any()


def test_exclude_widens_each_span_by_the_margin():
    arrays = raw_minutes.place([piece(0.0, 20, 1.0, 40.0)])
    raw_minutes.exclude(arrays, [(600.0, 600.0)])
    gone = arrays["at"][np.isnan(arrays["frequency"])]
    margin = raw_minutes.UNUSABLE_MARGIN
    assert gone.min() >= 600.0 - margin and gone.max() <= 600.0 + margin
    assert len(gone) == 2 * margin // room_days.STEP + 1
    assert not np.isnan(arrays["temperature"]).any()


def test_span_reads_from_and_to():
    assert raw_minutes.span("10:20.5") == (10.0, 20.5)
