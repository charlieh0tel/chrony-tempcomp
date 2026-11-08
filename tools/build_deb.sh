#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Builds the tempcomp-filter package.
#
# Usage: tools/build_deb.sh [OUTPUT_DIRECTORY]   (default dist)

set -euo pipefail

readonly VERSION=0.1.2
TOOLS=$(cd "$(dirname "$0")" && pwd)
readonly TOOLS
readonly OUTPUT=${1:-dist}

main() {
  # Global: the EXIT trap runs after main has returned.
  root=$(mktemp -d)
  trap 'rm -rf "${root}"' EXIT
  # mktemp's 0700 would be the package's "./".
  chmod 755 "${root}"
  install -D -m 755 "${TOOLS}/tempcomp-filter.sh" "${root}/usr/sbin/tempcomp-filter"
  install -D -m 644 "${TOOLS}/tempcomp-filter.service" \
    "${root}/lib/systemd/system/tempcomp-filter.service"
  install -D -m 644 "${TOOLS}/tempcomp-filter.default" \
    "${root}/etc/default/tempcomp-filter"
  install -D -m 644 "${TOOLS}/../LICENSE" \
    "${root}/usr/share/doc/tempcomp-filter/copyright"
  install -d "${root}/DEBIAN"
  sed "s/@VERSION@/${VERSION}/" "${TOOLS}/deb/control" > "${root}/DEBIAN/control"
  echo /etc/default/tempcomp-filter > "${root}/DEBIAN/conffiles"
  install -m 755 "${TOOLS}/deb/postinst" "${TOOLS}/deb/prerm" \
    "${TOOLS}/deb/postrm" "${root}/DEBIAN/"
  mkdir -p "${OUTPUT}"
  dpkg-deb --root-owner-group --build "${root}" \
    "${OUTPUT}/tempcomp-filter_${VERSION}_all.deb"
}

main "$@"
