# SPDX-License-Identifier: GPL-3.0-or-later
"""chrony_tempcomp.py's pairing of clock updates with sensor readings."""

import chrony_tempcomp
import numpy as np
import pytest

MAX_GAP = 60.0


def pair(tt, T, C, rt, F):
    return chrony_tempcomp.pair(
        *(np.array(v, float) for v in (tt, T, C, rt, F)), MAX_GAP
    )


def test_takes_the_reading_in_force_not_a_later_one():
    at, temperature, own = pair([0, 15], [50, 60], [1.0, 2.0], [10], [5.0])
    assert at.tolist() == [10]
    assert temperature.tolist() == [50]
    assert own[0] == pytest.approx(5.0 * (1 - 1e-6 * 1.0) - 1.0)


def test_is_the_inverse_of_chronys_frequency():
    own = 500.0
    comp = 3.0
    logged = (own + comp) / (1 - 1e-6 * comp)  # LCL_ReadAbsoluteFrequency
    assert pair([0], [50], [comp], [1], [logged])[2][0] == pytest.approx(own, abs=1e-9)


def test_drops_an_update_in_the_second_the_compensation_changed():
    at, _, _ = pair([0, 15, 30], [50, 60, 60], [1, 2, 2], [15, 30], [5, 5])
    assert at.tolist() == [30]


def test_drops_updates_before_any_reading_or_with_a_stale_one():
    at, _, _ = pair([100, 110], [50, 50], [1, 1], [50, 105, 110 + MAX_GAP + 1], [5] * 3)
    assert at.tolist() == [105]
