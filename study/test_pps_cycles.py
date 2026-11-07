# SPDX-License-Identifier: GPL-3.0-or-later
"""pps_cycles.py, on simulated cycles with known answers."""

import sqlite3

import numpy as np
import pps_cycles
import pytest
from common import lag

SEED = 1
START = 1_800_000_000.0
STEP = float(pps_cycles.BIN)
HALF_CYCLE = 900.0
CYCLES = 4
SETTLE_BEFORE = 3600.0
# The sensor's own response to the load, s, and its rise, degrees.
SENSOR_TAU = 60.0
RISE = 20.0
# The crystal's curve on what reaches it, ppm per degree and per degree squared.
SLOPE = -0.1
CURVE = 0.002


def simulate(crystal_lag, noise=0.0, room_ppm_per_degree=0.0):
    """Cycles of load after a quiet hour, the crystal `crystal_lag` s behind
    the sensor, with a room warming throughout."""
    on = START + SETTLE_BEFORE + np.arange(CYCLES) * 2 * HALF_CYCLE
    off = on + HALF_CYCLE
    end = off[-1] + HALF_CYCLE
    at = np.arange(START, end, STEP)
    load = np.zeros(len(at))
    for start, stop in zip(on, off):
        load[(at >= start) & (at < stop)] = 1.0
    sensor = lag(load * RISE, SENSOR_TAU, STEP)
    ambient = 0.5 * (at - at[0]) / 3600.0
    reached = lag(sensor, crystal_lag, STEP)
    rng = np.random.default_rng(SEED)
    ppm = (
        SLOPE * reached
        + CURVE * reached**2
        + room_ppm_per_degree * ambient
        + noise * rng.standard_normal(len(at))
    )
    return pps_cycles.Series(
        at,
        ppm,
        sensor + 50.0,
        ambient + 20.0,
        on,
        off,
        START - 2 * pps_cycles.SETTLE,
        np.empty((0, 2)),
    )


def test_read_cycles_pairs_starts_and_ends(tmp_path):
    path = tmp_path / "cycles.log"
    path.write_text("on=10\noff=20\non=30\noff=40\ndone\n")
    on, off = pps_cycles.read_cycles(str(path))
    assert on.tolist() == [10.0, 30.0]
    assert off.tolist() == [20.0, 40.0]


def test_read_cycles_refuses_a_start_with_no_end(tmp_path):
    path = tmp_path / "cycles.log"
    path.write_text("on=10\noff=20\non=30\n")
    with pytest.raises(ValueError):
        pps_cycles.read_cycles(str(path))


def test_read_ambient_takes_only_the_room_in_order(tmp_path):
    path = tmp_path / "sensors.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE reading (sensor_id, at, value)")
    connection.executemany(
        "INSERT INTO reading VALUES (?, ?, ?)",
        [(1, 2_000_000_000, 21.0), (2, 1_500_000_000, 99.0), (1, 1_000_000_000, 20.0)],
    )
    connection.commit()
    connection.close()
    at, value = pps_cycles.read_ambient(str(path))
    assert at.tolist() == [1.0, 2.0]
    assert value.tolist() == [20.0, 21.0]


def test_usable_drops_a_span_and_its_margin_and_unknown_bins():
    series = simulate(45.0)
    middle = series.at[len(series.at) // 2]
    series.unusable = np.array([[middle, middle + 100.0]])
    series.ppm[0] = np.nan
    good = pps_cycles.usable(series)
    assert not good[0]
    margin = pps_cycles.UNUSABLE_MARGIN
    assert not good[
        (series.at >= middle - margin) & (series.at <= middle + 100.0 + margin)
    ].any()
    assert good[series.at > middle + 100.0 + margin].all()


@pytest.mark.parametrize("crystal_lag", [0.0, 45.0, 120.0, 300.0])
def test_fast_lag_finds_the_crystals_lag(crystal_lag):
    result = pps_cycles.fast_lag(simulate(crystal_lag, noise=1e-3), None)
    minutes = crystal_lag / 60.0
    assert pps_cycles.FAST_LAGS[int(np.argmin(result.held_out))] == pytest.approx(
        minutes
    )
    assert result.best_by_fold == [pytest.approx(minutes)] * CYCLES
    assert pps_cycles.FAST_LAGS[int(np.argmin(result.load_to_rest))] == pytest.approx(
        minutes
    )
    assert pps_cycles.FAST_LAGS[int(np.argmin(result.rest_to_load))] == pytest.approx(
        minutes
    )


def test_a_folds_best_lag_is_that_cycles_alone():
    series = simulate(45.0, noise=1e-3)
    held = pps_cycles.cycle_of(series) == 2
    series.ppm[held] = simulate(300.0, noise=1e-3).ppm[held]
    best = pps_cycles.fast_lag(series, None).best_by_fold
    assert best[2] > max(best[0], best[1], best[3])


def test_fast_lag_with_the_room_recovers_a_lag_the_room_would_bias():
    series = simulate(45.0, noise=1e-3, room_ppm_per_degree=-0.05)
    result = pps_cycles.fast_lag(series, 0.0)
    assert pps_cycles.FAST_LAGS[int(np.argmin(result.held_out))] == pytest.approx(0.75)


def test_fast_lag_ignores_an_unusable_span():
    series = simulate(45.0, noise=1e-3)
    bad = series.at[
        (series.at >= series.load_on[1]) & (series.at < series.load_on[1] + 300)
    ]
    clean = pps_cycles.fast_lag(series, None).held_out
    series.ppm[np.isin(series.at, bad)] += 50.0
    series.unusable = np.array([[bad[0], bad[-1]]])
    masked = pps_cycles.fast_lag(series, None).held_out
    assert int(np.argmin(masked)) == int(np.argmin(clean))
    assert max(masked) < 1.0


def test_cycle_of_counts_cycles_and_leaves_out_before_and_after():
    series = simulate(0.0)
    cycle = pps_cycles.cycle_of(series)
    assert (cycle[series.at < series.load_on[0]] == -1).all()
    assert cycle[np.searchsorted(series.at, series.load_on[2])] == 2
    under = pps_cycles.loaded(series, cycle)
    assert under[np.searchsorted(series.at, series.load_on[1])]
    assert not under[np.searchsorted(series.at, series.load_off[1])]


def test_score_splits_bias_from_spread():
    result = pps_cycles.score(np.array([1.0, 3.0]))
    assert result.bias == pytest.approx(2.0)
    assert result.spread == pytest.approx(1.0)
    assert result.rms == pytest.approx(np.sqrt(5.0))


def test_a_saved_series_round_trips_each_width(tmp_path):
    series = simulate(0.0)
    path = tmp_path / "series.npz"
    arrays = {}
    for width, factor in ((pps_cycles.BIN, 1), (pps_cycles.COARSE_BIN, 2)):
        arrays[f"at_{width}"] = series.at * factor
        arrays[f"ppm_{width}"] = series.ppm * factor
        arrays[f"sensor_{width}"] = series.sensor * factor
        arrays[f"ambient_{width}"] = series.ambient * factor
    np.savez(
        path,
        load_on=series.load_on,
        load_off=series.load_off,
        first_off=series.first_off,
        unusable=np.array([[1.0, 2.0]]),
        **arrays,
    )
    fine = pps_cycles.load_series(str(path), pps_cycles.BIN)
    coarse = pps_cycles.load_series(str(path), pps_cycles.COARSE_BIN)
    assert fine.first_off == series.first_off
    assert fine.unusable.tolist() == [[1.0, 2.0]]
    assert np.array_equal(fine.ppm, series.ppm)
    assert np.array_equal(coarse.ppm, series.ppm * 2)
