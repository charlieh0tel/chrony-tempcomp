#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record PPS edges against CLOCK_MONOTONIC_RAW, with every temperature
sensor and the CPU's load, one row per edge.

Read-only: it reads sysfs, /proc and the clocks, needs no privileges,
and sends chronyd nothing.  Standard library only, one file, so it can
be copied to a host and run.  It records what it reads and computes
nothing about frequency: `pps_step.py` does that afterward.

Usage: python3 pps_raw.py -o draws-pps.csv [--device /sys/class/pps/pps0]

The kernel stamps an edge in CLOCK_REALTIME, the only stamp sysfs gives
(`assert`, "sec.nsec#seq", drivers/pps/sysfs.c).  So each row also has
the two clocks read together just after the edge: the time back to the
edge is short, so chronyd's steering of CLOCK_REALTIME hardly enters
the raw time of the edge.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import platform
import sys
import time

DEVICE = "/sys/class/pps/pps0"
# Every temperature the kernel offers, in millidegrees Celsius.
SENSOR_GLOBS = (
    "/sys/class/thermal/thermal_zone*/temp",
    "/sys/class/hwmon/hwmon*/temp*_input",
)
# What says which sensor a path is: hwmon numbers can change at boot.
SENSOR_NAME_FILES = ("type", "name")
CLOCKSOURCE = "/sys/devices/system/clocksource/clocksource0/current_clocksource"
BOOT_ID = "/proc/sys/kernel/random/boot_id"
PROC_STAT = "/proc/stat"
# /proc/stat's first line, after "cpu": which fields are idle time
# (idle, iowait); the rest are busy.  proc(5).
IDLE_FIELDS = (3, 4)

NANOSECONDS = 1_000_000_000
# Seconds past an edge's expected time to first look for it.
WAKE_MARGIN = 0.005
# Seconds between looks for an edge, while one is due and while overdue.
POLL = 0.002
POLL_OVERDUE = 0.05
# Seconds without an edge before it counts as overdue.
OVERDUE = 2.0
# Tries at reading the clocks together, of which the tightest is kept.
PAIR_TRIES = 8

# A row's columns, before one column per sensor, named by its path:
# - seq: the kernel's count of edges
# - stamp_edge_ns: the edge, in the clock that stamped it
# - stamp_read_ns, raw_read_ns: that clock and the raw read together after it
# - unix_read_ns: CLOCK_REALTIME just after them, to place the row in time
# - pair_ns: how far apart that reading could be (raw clock, read either
#   side of the realtime clock)
# - sensors_ns: how long reading this row's sensors took
# - clocksource: the kernel's current clocksource
# - cpu_busy: fraction of the CPUs' time busy since the last row
COLUMNS = (
    "seq",
    "stamp_edge_ns",
    "stamp_read_ns",
    "raw_read_ns",
    "unix_read_ns",
    "pair_ns",
    "sensors_ns",
    "clocksource",
    "cpu_busy",
)


def parse_assert(text: str) -> tuple[int, int]:
    """(stamp in ns, sequence) from a PPS `assert` file's contents."""
    stamp, sequence = text.strip().split("#")
    seconds, nanoseconds = stamp.split(".")
    return int(seconds) * NANOSECONDS + int(nanoseconds), int(sequence)


def read_text(path: str) -> str:
    with open(path) as file:
        return file.read().strip()


def read_assert(path: str) -> tuple[int, int]:
    text = read_text(path)
    if not text:
        raise SystemExit(f"{path} is empty: the device does not capture assert")
    return parse_assert(text)


def read_clocks(stamp_clock: int) -> tuple[int, int, int]:
    """(stamping clock, raw, width), all ns: the tightest of a few readings
    of the stamping clock between two of the raw clock."""
    best = None
    for _ in range(PAIR_TRIES):
        before = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
        stamp = time.clock_gettime_ns(stamp_clock)
        after = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
        if best is None or after - before < best[2]:
            best = (stamp, (before + after) // 2, after - before)
    assert best is not None
    return best


def find_sensors() -> list[str]:
    return sorted(path for pattern in SENSOR_GLOBS for path in glob.glob(pattern))


def sensor_name(path: str) -> str:
    for name in SENSOR_NAME_FILES:
        try:
            return read_text(os.path.join(os.path.dirname(path), name))
        except OSError:
            continue
    return "unknown"


class Sensors:
    """Reads the sensors; a read that fails gives an empty column, and
    says so on stderr the first time for each sensor."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        self.failed: set[str] = set()

    def read(self) -> list[str]:
        return [self._read_one(path) for path in self.paths]

    def _read_one(self, path: str) -> str:
        try:
            return read_text(path)
        except OSError as error:
            if path not in self.failed:
                self.failed.add(path)
                print(f"cannot read {path}: {error}", file=sys.stderr)
            return ""


def read_cpu_times(path: str = PROC_STAT) -> tuple[int, int]:
    """(busy, total) jiffies since boot, all CPUs."""
    with open(path) as file:
        fields = [int(field) for field in file.readline().split()[1:]]
    idle = sum(fields[i] for i in IDLE_FIELDS)
    return sum(fields) - idle, sum(fields)


def busy_fraction(before: tuple[int, int], after: tuple[int, int]) -> float:
    total = after[1] - before[1]
    return (after[0] - before[0]) / total if total > 0 else 0.0


class SysfsSource:
    """Edges from a PPS device's `assert` file, polled just after each is
    due; the kernel stamps them in CLOCK_REALTIME."""

    stamp_clock = time.CLOCK_REALTIME

    def __init__(self, device: str) -> None:
        self.device = device
        self.assert_path = os.path.join(device, "assert")
        self.stamp = 0
        # The edge already there may be old: only a later one is a row.
        self.sequence = read_assert(self.assert_path)[1]

    def header(self) -> list[tuple[str, str]]:
        return [
            ("device", self.device),
            ("pps_name", read_text(os.path.join(self.device, "name"))),
            ("pps_path", read_text(os.path.join(self.device, "path"))),
        ]

    def next_edge(self) -> tuple[int, int]:
        """Sleep until a new edge shows, then return (stamp, sequence)."""
        if self.stamp:
            pause = self.stamp / NANOSECONDS + 1 + WAKE_MARGIN - time.time()
            if pause > 0:
                time.sleep(pause)
        overdue = False
        started = time.monotonic()
        while True:
            stamp, sequence = read_assert(self.assert_path)
            if sequence != self.sequence:
                if overdue:
                    print("edges again", file=sys.stderr)
                self.stamp, self.sequence = stamp, sequence
                return stamp, sequence
            if not overdue and time.monotonic() - started > OVERDUE:
                overdue = True
                print(f"no edge for {OVERDUE} s", file=sys.stderr)
            time.sleep(POLL_OVERDUE if overdue else POLL)


def write_header(file, source, sensors: list[str]) -> None:
    """Comment lines saying what the rows are from, then the column names."""
    for key, value in [
        ("host", platform.node()),
        ("kernel", platform.release()),
        ("boot_id", read_text(BOOT_ID)),
        *source.header(),
    ]:
        file.write(f"# {key}={value}\n")
    for path in sensors:
        file.write(f"# sensor={path} {sensor_name(path)}\n")


def record(
    output: str,
    source,
    sensor_paths: list[str] | None = None,
    edges: int = 0,
) -> None:
    """Write a row per edge of `source` to `output`, a new file, until
    stopped, or for `edges` edges if not zero."""
    paths = find_sensors() if sensor_paths is None else sensor_paths
    sensors = Sensors(paths)
    with open(output, "x", newline="") as file:
        write_header(file, source, paths)
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(list(COLUMNS) + paths)
        file.flush()
        cpu = read_cpu_times()
        written = 0
        while not edges or written < edges:
            stamp, sequence = source.next_edge()
            stamp_read, raw, width = read_clocks(source.stamp_clock)
            unix = time.clock_gettime_ns(time.CLOCK_REALTIME)
            started = time.monotonic_ns()
            readings = sensors.read()
            took = time.monotonic_ns() - started
            now = read_cpu_times()
            writer.writerow(
                [
                    sequence,
                    stamp,
                    stamp_read,
                    raw,
                    unix,
                    width,
                    took,
                    read_text(CLOCKSOURCE),
                    f"{busy_fraction(cpu, now):.3f}",
                ]
                + readings
            )
            file.flush()
            cpu = now
            written += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", required=True, help="new CSV file")
    parser.add_argument("--device", default=DEVICE, help=f"sysfs device ({DEVICE})")
    parser.add_argument(
        "--edges", type=int, default=0, help="stop after this many edges (never)"
    )
    args = parser.parse_args()
    try:
        record(args.output, SysfsSource(args.device), edges=args.edges)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
