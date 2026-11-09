# SPDX-License-Identifier: GPL-3.0-or-later
"""build_deb.sh: what the package holds."""

import shutil
import subprocess
from pathlib import Path

import pytest

BUILD = Path(__file__).with_name("build_deb.sh")


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_the_package_holds_the_filter_its_unit_and_its_settings(tmp_path):
    subprocess.run([BUILD, str(tmp_path)], check=True, capture_output=True)
    (package,) = tmp_path.glob("tempcomp-filter_*_all.deb")
    listing = contents(package)
    for path in (
        "./usr/sbin/tempcomp-filter",
        "./lib/systemd/system/tempcomp-filter.service",
        "./etc/default/tempcomp-filter",
    ):
        assert path in listing
    assert listing.splitlines()[0].startswith("drwxr-xr-x root/root")
    assert "-rwxr-xr-x root/root" in next(
        line
        for line in listing.splitlines()
        if line.endswith("usr/sbin/tempcomp-filter")
    )
    control = tmp_path / "control"
    subprocess.run(["dpkg-deb", "--control", package, control], check=True)
    assert (control / "conffiles").read_text() == "/etc/default/tempcomp-filter\n"
    fields = (control / "control").read_text()
    assert "Architecture: all" in fields
    assert "@VERSION@" not in fields
    for script in ("postinst", "prerm", "postrm"):
        assert (control / script).stat().st_mode & 0o111


def contents(package):
    return subprocess.run(
        ["dpkg-deb", "--contents", package], check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_a_host_package_holds_its_settings_and_tempcomp_line(tmp_path):
    subprocess.run([BUILD, "--host", "draws", str(tmp_path)], check=True)
    (package,) = tmp_path.glob("tempcomp-filter-draws_*_all.deb")
    listing = contents(package)
    for path in (
        "./etc/tempcomp-filter.d/host.conf",
        "./etc/chrony/conf.d/tempcomp-filter.conf",
    ):
        assert path in listing
    assert "usr/sbin" not in listing
    control = tmp_path / "control"
    subprocess.run(["dpkg-deb", "--control", package, control], check=True)
    assert (control / "conffiles").read_text().split() == [
        "/etc/tempcomp-filter.d/host.conf",
        "/etc/chrony/conf.d/tempcomp-filter.conf",
    ]
    fields = (control / "control").read_text()
    assert "Package: tempcomp-filter-draws\n" in fields
    assert "Depends: tempcomp-filter (>= " in fields
    assert "@VERSION@" not in fields and "@HOST@" not in fields


def test_each_hosts_tempcomp_line_reads_the_filters_output():
    for conf in BUILD.parent.glob("hosts/*/tempcomp-filter.conf"):
        (line,) = [x for x in conf.read_text().splitlines() if x.startswith("tempcomp")]
        assert line.split()[1] == "/run/chrony/tempcomp-filter"


def test_the_unit_writes_where_chronyds_apparmor_profile_reads():
    unit = BUILD.with_name("tempcomp-filter.service").read_text()
    settings = [line for line in unit.splitlines() if not line.startswith("#")]
    # /run/chrony is 0700, chrony's user's; ProtectSystem=strict would make
    # it read-only.
    assert "User=_chrony" in settings
    assert "ReadWritePaths=-/run/chrony" in settings
    assert "After=chrony.service" in settings
    assert "WantedBy=chrony.service" in settings
