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


def settings(tmp_path, sensor, **changes):
    return {
        "PATH": os.environ["PATH"],
        "DEFAULTS": str(tmp_path / "none"),
        "HOST_SETTINGS": str(tmp_path / "none.d"),
        "SENSOR": str(sensor),
        "TAU": "300",
        "PERIOD": "0.1",
        "OUTPUT": str(tmp_path / "temp"),
        "STATE": str(tmp_path / "temp.state"),
        "OFF_VALUE": "54514",
    } | changes


def start(tmp_path, sensor, **changes):
    return subprocess.Popen(
        [SCRIPT, "run"],
        env=settings(tmp_path, sensor, **changes),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def command(tmp_path, sensor, name, **changes):
    return subprocess.run(
        [SCRIPT, name],
        check=False,
        env=settings(tmp_path, sensor, **changes),
        capture_output=True,
        text=True,
    )


def stop(process):
    process.send_signal(signal.SIGTERM)
    return process.wait(timeout=WAIT)


def test_the_loop_starts_from_the_reading_and_a_stop_keeps_the_last(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor)
    try:
        assert float(wait_for(tmp_path / "temp")) == 50000
        sensor.write_text("60000\n")
        moved = float(wait_for(tmp_path / "temp", lambda text: float(text) > 50000))
        assert 50000 < moved < 50100
    finally:
        stop(process)
    assert 50000 < float((tmp_path / "temp").read_text()) < 50100


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


def test_a_sensor_that_goes_is_logged_and_the_value_kept(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor)
    wait_for(tmp_path / "temp")
    sensor.unlink()
    time.sleep(0.5)
    stop(process)
    assert "cannot read" in process.stderr.read()
    assert float((tmp_path / "temp").read_text()) == 50000


def test_off_writes_no_compensation(tmp_path):
    result = command(tmp_path, tmp_path / "sensor", "off")
    assert result.returncode == 0
    assert (tmp_path / "temp").read_text() == "54514\n"


def test_check_wants_each_setting_and_a_readable_sensor(tmp_path):
    sensor = tmp_path / "sensor"
    assert "cannot read" in command(tmp_path, sensor, "check").stderr
    sensor.write_text("50000\n")
    assert command(tmp_path, sensor, "check").returncode == 0
    unset = command(tmp_path, sensor, "check", OFF_VALUE="")
    assert unset.returncode == 1
    assert "set OFF_VALUE" in unset.stderr


def test_settings_come_from_the_defaults_file(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    defaults = tmp_path / "defaults"
    defaults.write_text("OFF_VALUE=12345\n")
    result = command(tmp_path, sensor, "off", DEFAULTS=str(defaults), OFF_VALUE="")
    assert result.returncode == 0
    assert (tmp_path / "temp").read_text() == "12345\n"


def test_host_settings_come_after_the_defaults(tmp_path):
    sensor = tmp_path / "sensor"
    defaults = tmp_path / "defaults"
    defaults.write_text("OFF_VALUE=12345\n")
    host = tmp_path / "host.d"
    host.mkdir()
    (host / "host.conf").write_text("OFF_VALUE=23456\n")
    result = command(
        tmp_path,
        sensor,
        "off",
        DEFAULTS=str(defaults),
        HOST_SETTINGS=str(host),
        OFF_VALUE="",
    )
    assert result.returncode == 0
    assert (tmp_path / "temp").read_text() == "23456\n"


def tau_at(tmp_path, unix_seconds, **changes):
    result = subprocess.run(
        [SCRIPT, "tau", str(unix_seconds)],
        check=True,
        env=settings(tmp_path, tmp_path / "sensor", **changes),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_an_alternate_time_constant_holds_on_odd_utc_days(tmp_path):
    day = 86400
    assert tau_at(tmp_path, 20000 * day + 5) == "300"
    assert tau_at(tmp_path, 20001 * day - 1, ALTERNATE_TAU="0") == "300"
    assert tau_at(tmp_path, 20001 * day, ALTERNATE_TAU="0") == "0"
    assert tau_at(tmp_path, 20002 * day, ALTERNATE_TAU="0") == "300"
    even = {"ALTERNATE_TAU": "off", "ALTERNATE_DAYS": "even"}
    assert tau_at(tmp_path, 20001 * day, **even) == "300"
    assert tau_at(tmp_path, 20002 * day, **even) == "off"


def today_alternates():
    """Settings that make today an ALTERNATE_TAU day."""
    odd = time.time() // 86400 % 2 == 1
    return {"ALTERNATE_DAYS": "odd" if odd else "even"}


def test_off_days_write_no_compensation_and_filter_on(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    with open("/proc/uptime") as uptime:
        now = float(uptime.read().split()[0])
    (tmp_path / "temp.state").write_text(f"40000.0 {now}\n")
    process = start(tmp_path, sensor, ALTERNATE_TAU="off", **today_alternates())
    try:
        assert wait_for(tmp_path / "temp") == "54514\n"
        state = wait_for(
            tmp_path / "temp.state", lambda text: float(text.split()[0]) > 40000
        )
        assert 40000 < float(state.split()[0]) < 40100
    finally:
        stop(process)
    assert "off (writing 54514, filtering at tau 300 s)" in process.stdout.read()


def test_no_time_constant_passes_readings_without_logging_gaps(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor, TAU="0")
    wait_for(tmp_path / "temp")
    sensor.write_text("60000\n")
    assert (
        float(wait_for(tmp_path / "temp", lambda text: float(text) != 50000)) == 60000
    )
    stop(process)
    assert "restarting" not in process.stdout.read()


def test_an_output_that_goes_is_logged_once_and_written_again(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    directory = tmp_path / "chrony"
    directory.mkdir()
    output = directory / "temp"
    process = start(tmp_path, sensor, OUTPUT=str(output))
    wait_for(output)
    output.unlink()
    directory.rmdir()
    time.sleep(0.5)
    directory.mkdir()
    wait_for(output)
    # The recovery is logged after the output is written, before the state.
    state = tmp_path / "temp.state"
    before = state.read_text()
    wait_for(state, lambda text: text not in ("", before))
    stop(process)
    assert process.stderr.read().count("cannot write") == 1
    assert f"{output} written again after " in process.stdout.read()


def test_the_log_has_the_start_and_a_summary_each_interval(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor, STATS_INTERVAL="0.3")
    wait_for(tmp_path / "temp")
    sensor.write_text("50400\n")
    time.sleep(1.0)
    stop(process)
    log = process.stdout.read()
    assert "start: " + str(sensor) + ", tau 300 s; from the first reading" in log
    summaries = [line for line in log.splitlines() if " readings, " in line]
    assert len(summaries) >= 2
    assert any("sensor 50000 to 50400" in line for line in summaries)
    assert all("0 failed" in line for line in summaries)


def test_a_restart_says_what_it_carries_on_from(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    with open("/proc/uptime") as uptime:
        now = float(uptime.read().split()[0])
    (tmp_path / "temp.state").write_text(f"49000.0 {now}\n")
    process = start(tmp_path, sensor)
    wait_for(tmp_path / "temp")
    stop(process)
    assert "carrying on from 49000.0" in process.stdout.read()


def test_a_gap_restart_is_logged(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("60000\n")
    (tmp_path / "temp.state").write_text("50000.0 0\n")
    process = start(tmp_path, sensor, TAU="1")
    wait_for(tmp_path / "temp")
    stop(process)
    assert "restarting from the reading, 60000, after" in process.stdout.read()


def test_a_failure_is_logged_once_and_its_recovery_with_its_length(tmp_path):
    sensor = tmp_path / "sensor"
    sensor.write_text("50000\n")
    process = start(tmp_path, sensor)
    wait_for(tmp_path / "temp")
    sensor.unlink()
    time.sleep(0.6)
    sensor.write_text("50000\n")
    time.sleep(0.4)
    stop(process)
    assert process.stderr.read().count("cannot read") == 1
    assert f"{sensor} read again after " in process.stdout.read()
