# SPDX-License-Identifier: GPL-3.0-or-later
"""tempcomp-filter.sh: its step, and its loop on a fake sensor."""

import math
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("tempcomp-filter.sh")
# Seconds to wait for the loop to write.
WAIT = 10.0


def step(f, reading, dt, tau):
    result = subprocess.run(
        [SCRIPT, "step", str(f), str(reading), str(dt), str(tau)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout)


def test_a_step_moves_toward_the_reading_by_its_time_constant():
    expected = 50000 + (1 - math.exp(-5 / 300)) * 1000
    assert step(50000, 51000, 5, 300) == pytest.approx(expected, abs=0.05)


def test_a_step_of_one_time_constant_covers_most_of_the_way():
    assert step(0, 1000, 300, 300) == pytest.approx(1000 * (1 - math.exp(-1)), abs=0.05)


def test_no_time_constant_passes_the_reading_through():
    assert step(50000, 51234, 5, 0) == 51234


def wait_for(path, predicate=lambda text: True):
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        if path.exists() and predicate(path.read_text()):
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


def start(tmp_path, sensor, **settings):
    environment = {
        "PATH": os.environ["PATH"],
        "SENSOR": str(sensor),
        "TAU": "300",
        "PERIOD": "0.1",
        "OUTPUT": str(tmp_path / "temp"),
        "OFF_VALUE": "54514",
    } | settings
    return subprocess.Popen(
        [SCRIPT, "run"], env=environment, stderr=subprocess.PIPE, text=True
    )


def stop(process):
    process.send_signal(signal.SIGTERM)
    return process.wait(timeout=WAIT)


def test_the_loop_starts_from_the_reading_and_turns_off_on_stop(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor)
    try:
        assert float(wait_for(tmp_path / "temp")) == 50000
        sensor.write_text("60000\n")
        moved = float(wait_for(tmp_path / "temp", lambda text: float(text) > 50000))
        assert 50000 < moved < 50100
    finally:
        assert stop(process) == 0
    assert (tmp_path / "temp").read_text() == "54514\n"


def test_a_restart_carries_on_from_the_state(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("60000\n")
    with open("/proc/uptime") as uptime:
        now = float(uptime.read().split()[0])
    (tmp_path / "temp.state").write_text(f"50000.0 {now}\n")
    process = start(tmp_path, sensor)
    try:
        assert 50000 <= float(wait_for(tmp_path / "temp")) < 50100
    finally:
        stop(process)


def test_a_long_gap_restarts_from_the_reading(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("60000\n")
    (tmp_path / "temp.state").write_text("50000.0 0\n")
    process = start(tmp_path, sensor, TAU="1")
    try:
        assert float(wait_for(tmp_path / "temp")) == 60000
    finally:
        stop(process)


def test_an_unreadable_sensor_is_logged_and_skipped(tmp_path):
    process = start(tmp_path, tmp_path / "missing")
    time.sleep(0.5)
    stop(process)
    assert "cannot read" in process.stderr.read()
