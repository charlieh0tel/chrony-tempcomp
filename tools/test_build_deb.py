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
    listing = subprocess.run(
        ["dpkg-deb", "--contents", package], check=True, capture_output=True, text=True
    ).stdout
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


def test_the_unit_leaves_its_output_where_chronyd_can_read_it():
    unit = BUILD.with_name("tempcomp-filter.service").read_text()
    settings = [line for line in unit.splitlines() if not line.startswith("#")]
    # DynamicUser puts RuntimeDirectory under /run/private, 0700.
    assert not any(line.startswith("DynamicUser=yes") for line in settings)
    assert "RuntimeDirectoryMode=0755" in settings
