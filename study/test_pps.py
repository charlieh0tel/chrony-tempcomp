# SPDX-License-Identifier: GPL-3.0-or-later
"""pps_raw.py and pps_step.py, on simulated edges with known answers."""

import csv
import threading
import time

import numpy as np
import pps_raw
import pps_step
import pytest

NS = pps_raw.NANOSECONDS
START_UNIX = 1_800_000_000
SENSOR = "/sys/fake/temp"
SEED = 1


def simulate(
    path,
    ppm_of_t,
    seconds=3600,
    real_rate_ppm=30.0,
    jitter=1e-6,
    busy_of_t=None,
    sensor_of_t=None,
    missing=(),
    late=(),
    clocksources=None,
):
    """Write what pps_raw.py would for a crystal `ppm_of_t` fast at true
    time t, under a realtime clock `real_rate_ppm` off true (chronyd's
    steering), with each edge stamped late by jitter and read 1 to 30 ms
    after that."""
    rng = np.random.default_rng(SEED)
    edges = np.arange(seconds, dtype=float)
    ppm = np.array([ppm_of_t(t) for t in edges])
    accumulated = np.cumsum(ppm) * 1e-6
    rate = 1 + real_rate_ppm * 1e-6

    def real(at):
        return int(START_UNIX * NS + at * rate * NS)

    with open(path, "w", newline="") as file:
        file.write(f"# host=simulated\n# boot_id=abc\n# sensor={SENSOR} fake\n")
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(list(pps_raw.COLUMNS) + [SENSOR])
        for i, t in enumerate(edges):
            if i in missing:
                continue
            stamp_error = rng.normal(0, jitter) + (1e-3 if i in late else 0.0)
            read_after = stamp_error + rng.uniform(1e-3, 30e-3)
            raw = t + accumulated[i] + read_after * (1 + ppm[i] * 1e-6)
            writer.writerow(
                [
                    i,
                    real(t + stamp_error),
                    real(t + read_after),
                    int(raw * NS),
                    100,
                    1000,
                    clocksources(t) if clocksources else "tsc",
                    f"{busy_of_t(t) if busy_of_t else 0.0:.3f}",
                    int(sensor_of_t(t) * 1000) if sensor_of_t else 50000,
                ]
            )


def rising(t, start, base, jump, amplitude, tau):
    if t < start:
        return base
    return base + jump + amplitude * (1 - np.exp(-(t - start) / tau))


def test_parse_assert():
    assert pps_raw.parse_assert("1800000000.000001234#567\n") == (
        1800000000 * NS + 1234,
        567,
    )


def test_read_assert_empty_says_why(tmp_path):
    path = tmp_path / "assert"
    path.write_text("")
    with pytest.raises(SystemExit, match="does not capture assert"):
        pps_raw.read_assert(str(path))


def test_read_cpu_times_and_busy_fraction(tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  10 0 10 70 10 0 0 0 0 0\ncpu0 1 1 1 1 1\n")
    assert pps_raw.read_cpu_times(str(stat)) == (20, 100)
    assert pps_raw.busy_fraction((20, 100), (70, 200)) == 0.5
    assert pps_raw.busy_fraction((20, 100), (20, 100)) == 0.0


def test_sensors_failure_is_empty_and_said_once(tmp_path, capsys):
    good = tmp_path / "good"
    good.write_text("41000\n")
    bad = str(tmp_path / "absent")
    sensors = pps_raw.Sensors([str(good), bad])
    assert sensors.read() == ["41000", ""]
    assert sensors.read() == ["41000", ""]
    assert capsys.readouterr().err.count("cannot read") == 1


def test_record_writes_a_row_per_edge_and_refuses_to_overwrite(tmp_path):
    device = tmp_path / "pps0"
    device.mkdir()
    (device / "name").write_text("fake\n")
    (device / "path").write_text("/dev/fake\n")
    assert_file = device / "assert"
    sensor = tmp_path / "temp"
    sensor.write_text("42000\n")
    stop = threading.Event()

    def stamp(sequence):
        # Stamped nearly a second ago, so the next edge is due at once.
        seconds = time.time() - 0.95
        whole = int(seconds)
        fresh = tmp_path / "fresh"
        fresh.write_text(f"{whole}.{int((seconds - whole) * NS):09d}#{sequence}\n")
        fresh.replace(assert_file)

    def edges():
        sequence = 1
        while not stop.wait(0.05):
            sequence += 1
            stamp(sequence)

    stamp(1)
    thread = threading.Thread(target=edges)
    thread.start()
    output = tmp_path / "run.csv"
    try:
        pps_raw.record(str(output), str(device), [str(sensor)], edges=3)
    finally:
        stop.set()
        thread.join()
    run = pps_step.load_run(str(output))
    assert len(run.seq) == 3
    assert run.names[str(sensor)] == "unknown"
    assert run.meta["pps_name"] == "fake"
    assert list(run.sensors[str(sensor)]) == [42.0, 42.0, 42.0]
    assert np.all(np.diff(run.seq) > 0)
    assert np.all(np.diff(run.raw_read) > 0)
    with pytest.raises(FileExistsError):
        pps_raw.record(str(output), str(device), [str(sensor)], edges=1)


def test_frequency_has_chronyd_sign_and_no_chronyd_in_it(tmp_path):
    """Fast is positive, whatever chronyd is doing to the realtime clock."""
    for real_rate_ppm in (-100.0, 0.0, 100.0):
        path = tmp_path / f"run{real_rate_ppm}.csv"
        simulate(path, lambda t: 12.0, seconds=1200, real_rate_ppm=real_rate_ppm)
        bins = pps_step.bin_run(pps_step.load_run(str(path)))
        assert np.nanmedian(bins.ppm) == pytest.approx(12.0, abs=0.02)


def test_minute_noise_matches_the_edge_jitter(tmp_path):
    """A microsecond of edge jitter is well under 0.02 ppm per minute."""
    path = tmp_path / "run.csv"
    simulate(path, lambda t: 0.0, seconds=3600)
    bins = pps_step.bin_run(pps_step.load_run(str(path)))
    assert np.nanstd(bins.ppm) < 0.02
    assert bins.jitter == pytest.approx(1e-6, rel=0.3)


def test_missing_and_late_edges_do_not_move_the_frequency(tmp_path):
    path = tmp_path / "run.csv"
    simulate(
        path,
        lambda t: -3.0,
        seconds=1200,
        missing=set(range(100, 110)) | {400, 700},
        late={200, 500, 900},
    )
    bins = pps_step.bin_run(pps_step.load_run(str(path)))
    # The three late edges, and at most a few clean ones in the tails.
    assert 3 <= bins.dropped <= 6
    assert np.nanmax(np.abs(bins.ppm + 3.0)) < 0.05


def test_bin_with_too_few_edges_has_no_frequency(tmp_path):
    path = tmp_path / "run.csv"
    simulate(path, lambda t: 1.0, seconds=600, missing=set(range(125, 175)))
    bins = pps_step.bin_run(pps_step.load_run(str(path)))
    assert np.isnan(bins.ppm).sum() == 1


def test_realtime_step_between_edge_and_read_is_dropped(tmp_path):
    path = tmp_path / "run.csv"
    simulate(path, lambda t: 2.0, seconds=300)
    run = pps_step.load_run(str(path))
    run.real_read[50] += NS
    run.real_read[60] -= NS
    assert len(pps_step.edge_times(run)[0]) == 298


def test_load_run_short_last_row_and_empty_sensor(tmp_path):
    path = tmp_path / "run.csv"
    simulate(path, lambda t: 1.0, seconds=10)
    lines = path.read_text().splitlines()
    lines[-2] = lines[-2].rsplit(",", 1)[0] + ","
    path.write_text("\n".join(lines[:-1] + [lines[-1][:20]]) + "\n")
    run = pps_step.load_run(str(path))
    assert len(run.seq) == 9
    assert np.isnan(run.sensors[SENSOR][-1])


def test_load_run_refuses_two_clocksources(tmp_path):
    path = tmp_path / "run.csv"
    simulate(
        path,
        lambda t: 1.0,
        seconds=10,
        clocksources=lambda t: "tsc" if t < 5 else "hpet",
    )
    with pytest.raises(ValueError, match="clocksource"):
        pps_step.load_run(str(path))


def test_find_steps_ignores_a_flicker():
    t = np.arange(4000.0)
    busy = np.zeros_like(t)
    busy[1000:2500] = 1.0
    busy[1500:1520] = 0.0  # Long enough to pass the median, too short to count.
    busy[3000:3010] = 1.0  # Under the median's window.
    steps = pps_step.find_steps(t, busy)
    assert [rose for _, rose in steps] == [True, False]
    assert steps[0][0] == pytest.approx(1000, abs=2)
    assert steps[1][0] == pytest.approx(2500, abs=2)


@pytest.mark.parametrize("tau", [120.0, 300.0, 1200.0])
def test_fit_step_recovers_tau_and_amplitude(tmp_path, tau):
    path = tmp_path / "run.csv"
    start = 1800.0
    simulate(path, lambda t: rising(t, start, 5.0, 0.0, -2.0, tau), seconds=4 * 3600)
    bins = pps_step.bin_run(pps_step.load_run(str(path)))
    fit = pps_step.fit_step(bins.t, bins.ppm, start, False)
    assert fit.tau == pytest.approx(tau, rel=0.1)
    assert fit.amplitude == pytest.approx(-2.0, rel=0.02)
    assert fit.y0 == pytest.approx(5.0, abs=0.01)
    assert fit.tau_low <= fit.tau <= fit.tau_high


def test_fit_step_separates_an_immediate_jump(tmp_path):
    """An electrical jump at the step is not to be read as a short tau."""
    path = tmp_path / "run.csv"
    start = 1800.0
    simulate(path, lambda t: rising(t, start, 0.0, 0.5, -2.0, 600.0), seconds=3 * 3600)
    bins = pps_step.bin_run(pps_step.load_run(str(path)))
    fit = pps_step.fit_step(bins.t, bins.ppm, start, True)
    assert fit.jump == pytest.approx(0.5, abs=0.03)
    assert fit.tau == pytest.approx(600.0, rel=0.1)


def test_fit_step_needs_both_sides():
    t = np.arange(100.0)
    assert pps_step.fit_step(t, t, -1.0, False) is None
    assert pps_step.fit_step(t, t, 1000.0, False) is None


def lagged(u, tau):
    out = np.empty_like(u)
    state = u[0]
    for i, value in enumerate(u):
        state += (1 - np.exp(-1.0 / tau)) * (value - state)
        out[i] = state
    return out


def test_scan_lags_recovers_a_known_lag():
    rng = np.random.default_rng(SEED)
    minutes = 24 * 60
    t = np.arange(minutes * 60.0)
    wander = np.sin(2 * np.pi * t / 7200) + 0.5 * np.sin(2 * np.pi * t / 2700)
    crystal = lagged(wander, 600.0)
    u = wander[::60][:minutes]
    y = -0.2 * crystal[::60][:minutes] + rng.normal(0, 0.002, minutes)
    scan = pps_step.scan_lags(u, y, 60.0)
    assert pps_step.LAGS[scan.best] == 10
    assert scan.slope == pytest.approx(-0.2, rel=0.05)
    assert scan.scores[scan.best] < scan.scores[0]


def test_scan_lags_refuses_too_little_or_flat():
    assert pps_step.scan_lags(np.zeros(100), np.arange(100.0), 60.0) is None
    assert pps_step.scan_lags(np.arange(10.0), np.arange(10.0), 60.0) is None


def test_chronyd_own_subtracts_compensation_and_leaves_gaps(tmp_path):
    path = tmp_path / "host.npz"
    at = np.array([0.0, 60.0, 120.0, 5000.0, 5060.0])
    np.savez(
        path,
        at=at,
        frequency=np.array([3.0, 3.0, np.nan, 4.0, 4.0]),
        compensation=np.array([1.0, 1.0, 1.0, 1.0, 1.0]),
    )
    own = pps_step.chronyd_own(str(path), np.array([30.0, 2500.0, 5030.0, np.nan]))
    assert own[0] == 2.0
    assert np.isnan(own[1])
    assert own[2] == 3.0
    assert np.isnan(own[3])


def test_report_runs_end_to_end(tmp_path, capsys):
    path = tmp_path / "run.csv"
    on, off = 1800.0, 3600.0

    def crystal(t):
        if t < off:
            return rising(t, on, 5.0, 0.0, -2.0, 300.0)
        peak = rising(off, on, 5.0, 0.0, -2.0, 300.0)
        return rising(t, off, peak, 0.0, 5.0 - peak, 300.0)

    simulate(
        path,
        crystal,
        seconds=2 * 3600,
        busy_of_t=lambda t: 1.0 if on <= t < off else 0.05,
        sensor_of_t=lambda t: 40 + (5.0 - crystal(t)) * 5,
    )
    npz = tmp_path / "host.npz"
    run = pps_step.load_run(str(path))
    bins = pps_step.bin_run(run)
    np.savez(
        npz,
        at=bins.unix,
        frequency=bins.ppm + 7.0,
        compensation=np.zeros_like(bins.ppm),
    )
    pps_step.report(run, bins, str(npz))
    out = capsys.readouterr().out
    assert "2 load steps" in out
    assert "load on at 30.0 min" in out
    assert "load off at 60.0 min" in out
    assert "median +7.000 ppm" in out
