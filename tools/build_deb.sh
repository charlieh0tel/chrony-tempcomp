#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Builds the tempcomp-filter package, or with --host a host's settings
# package, tempcomp-filter-HOST, from tools/hosts/HOST: its filter settings
# and its chrony.conf tempcomp line.
#
# Usage: tools/build_deb.sh [--host HOST] [OUTPUT_DIRECTORY]   (default dist)

set -euo pipefail

readonly VERSION=0.1.3
TOOLS=$(cd "$(dirname "$0")" && pwd)
readonly TOOLS
readonly HOST_SETTINGS=/etc/tempcomp-filter.d/host.conf
readonly CHRONY_CONF=/etc/chrony/conf.d/tempcomp-filter.conf

# Writes the package's control file from template $1 for package $2.
control() {
  sed -e "s/@VERSION@/${VERSION}/g" -e "s/@HOST@/${2#tempcomp-filter-}/g" \
    "$1" > "${root}/DEBIAN/control"
}

filter() {
  install -D -m 755 "${TOOLS}/tempcomp-filter.sh" "${root}/usr/sbin/tempcomp-filter"
  install -D -m 644 "${TOOLS}/tempcomp-filter.service" \
    "${root}/lib/systemd/system/tempcomp-filter.service"
  install -D -m 644 "${TOOLS}/tempcomp-filter.default" \
    "${root}/etc/default/tempcomp-filter"
  control "${TOOLS}/deb/control" tempcomp-filter
  echo /etc/default/tempcomp-filter > "${root}/DEBIAN/conffiles"
  install -m 755 "${TOOLS}/deb/postinst" "${TOOLS}/deb/prerm" \
    "${TOOLS}/deb/postrm" "${root}/DEBIAN/"
}

host() {
  local name=$1 source="${TOOLS}/hosts/$1"
  install -D -m 644 "${source}/tempcomp-filter" "${root}${HOST_SETTINGS}"
  install -D -m 644 "${source}/tempcomp-filter.conf" "${root}${CHRONY_CONF}"
  control "${TOOLS}/deb/host/control" "tempcomp-filter-${name}"
  printf '%s\n' "${HOST_SETTINGS}" "${CHRONY_CONF}" > "${root}/DEBIAN/conffiles"
  install -m 755 "${TOOLS}/deb/host/postinst" \
    "${root}/DEBIAN/"
}

main() {
  local name=tempcomp-filter host_name=""
  if [[ ${1:-} == --host ]]; then
    host_name=$2
    name="tempcomp-filter-${host_name}"
    shift 2
  fi
  local output=${1:-dist}
  # Global: the EXIT trap runs after main has returned.
  root=$(mktemp -d)
  trap 'rm -rf "${root}"' EXIT
  # mktemp's 0700 would be the package's "./".
  chmod 755 "${root}"
  install -d "${root}/DEBIAN"
  if [[ -n "${host_name}" ]]; then
    host "${host_name}"
  else
    filter
  fi
  install -D -m 644 "${TOOLS}/../LICENSE" "${root}/usr/share/doc/${name}/copyright"
  mkdir -p "${output}"
  dpkg-deb --root-owner-group --build "${root}" \
    "${output}/${name}_${VERSION}_all.deb"
}

main "$@"
