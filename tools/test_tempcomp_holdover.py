# SPDX-License-Identifier: GPL-3.0-or-later
"""tempcomp-holdover.sh against a fake chronyc."""

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).with_name("tempcomp-holdover.sh")
CYCLE = 21600
LOCKED = 7200

# A fake chronyc: `-c -n selectdata -a` prints the sources in $SOURCES (a
# file of CSV lines), `selectopts NAME +/-noselect` edits that file's
# configured noselect, and every call is appended to $CALLS.  chronyc's CSV
# (chrony 4.6, client.c print_report) gives each option its own field.
FAKE_CHRONYC = r"""#!/bin/bash
echo "$*" >> "${CALLS}"
case "$1" in
  -c) cat "${SOURCES}" ;;
  selectopts)
    if ! grep -q "^[^,]*,$2," "${SOURCES}"; then exit 1; fi
    if [[ $3 == +noselect ]]; then noselect=N; else noselect=-; fi
    sed -i "s/^\([^,]*\),$2,\([^,]*\),[^,]*,/\1,$2,\2,${noselect},/" "${SOURCES}"
    ;;
esac
"""


def sources(*rows: tuple[str, str]) -> str:
    """CSV like chronyc's selectdata, from (name, configured noselect: N
    or -): state, name, auth, the configured then the effective options
    (N, P, T, R and one more, a field each), last, score, interval, leap."""
    return "".join(
        f"*,{name},N,{noselect},-,-,-,-,{noselect},-,-,-,-,1,1.0,-1e-6,1e-6,N\n"
        for name, noselect in rows
    )


def run(
    tmp_path: Path, command: str, start: int, end: int | None = None
) -> subprocess.CompletedProcess[str]:
    chronyc = tmp_path / "chronyc"
    if not chronyc.exists():
        chronyc.write_text(FAKE_CHRONYC)
        chronyc.chmod(0o755)
    settings = tmp_path / "holdover.conf"
    settings.write_text(
        f"START={start}\nEND={end if end is not None else start + 10 * CYCLE}\n"
        f"CYCLE={CYCLE}\nLOCKED={LOCKED}\n"
    )
    return subprocess.run(
        [SCRIPT, command],
        env={
            "PATH": os.environ["PATH"],
            "TEMPCOMP_HOLDOVER_SETTINGS": str(settings),
            "STATE_DIRECTORY": str(tmp_path / "state"),
            "CHRONYC": str(chronyc),
            "SOURCES": str(tmp_path / "sources"),
            "CALLS": str(tmp_path / "calls"),
        },
        check=True,
        capture_output=True,
        text=True,
    )


def options(tmp_path: Path) -> dict[str, str]:
    return {
        line.split(",")[1]: line.split(",")[3]
        for line in (tmp_path / "sources").read_text().splitlines()
    }


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def now() -> int:
    return int(time.time())


def test_while_locked_nothing_changes(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-")))
    run(tmp_path, "tick", start=now() - 60)
    assert options(tmp_path) == {"GPS": "-"}
    assert not any("selectopts" in call for call in calls(tmp_path))


def test_a_holdover_makes_every_selectable_source_noselect(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(
        sources(("GPS", "-"), ("NMEA", "N"), ("10.0.0.239", "-"))
    )
    result = run(tmp_path, "tick", start=now() - LOCKED - 60)
    assert options(tmp_path) == {"GPS": "N", "NMEA": "N", "10.0.0.239": "N"}
    assert (tmp_path / "state" / "changed").read_text().split() == ["GPS", "10.0.0.239"]
    assert "reselect" in calls(tmp_path)
    assert "noselect GPS" in result.stderr


def test_a_second_tick_in_a_holdover_changes_nothing_more(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    before = len(calls(tmp_path))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    assert [call for call in calls(tmp_path)[before:] if "selectopts" in call] == []


def test_a_source_added_during_a_holdover_is_made_noselect_too(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    with (tmp_path / "sources").open("a") as rows:
        rows.write(sources(("192.0.2.1", "-")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    assert options(tmp_path)["192.0.2.1"] == "N"


def test_after_a_holdover_only_what_it_changed_is_given_back(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-"), ("NMEA", "N")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    run(tmp_path, "tick", start=now() - CYCLE - 60)
    assert options(tmp_path) == {"GPS": "-", "NMEA": "N"}
    assert not (tmp_path / "state" / "changed").exists()


def test_a_source_gone_by_the_restore_is_logged_and_skipped(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-"), ("192.0.2.1", "-")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    (tmp_path / "sources").write_text(sources(("GPS", "N")))
    result = run(tmp_path, "restore", start=now())
    assert options(tmp_path) == {"GPS": "-"}
    assert "could not give back 192.0.2.1" in result.stderr


def test_after_the_end_the_sources_are_given_back(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-")))
    run(tmp_path, "tick", start=now() - LOCKED - 60)
    start = now() - LOCKED - 60
    run(tmp_path, "tick", start=start, end=now() - 1)
    assert options(tmp_path) == {"GPS": "-"}


def test_before_the_start_nothing_changes(tmp_path: Path) -> None:
    (tmp_path / "sources").write_text(sources(("GPS", "-")))
    run(tmp_path, "tick", start=now() + 3600)
    assert options(tmp_path) == {"GPS": "-"}


def test_bad_settings_are_refused(tmp_path: Path) -> None:
    settings = tmp_path / "holdover.conf"
    settings.write_text(f"START=0\nEND=1\nCYCLE={CYCLE}\nLOCKED={CYCLE}\n")
    result = subprocess.run(
        [SCRIPT, "tick"],
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "TEMPCOMP_HOLDOVER_SETTINGS": str(settings),
            "STATE_DIRECTORY": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "LOCKED must be less than CYCLE" in result.stderr
