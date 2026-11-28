#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Builds the tempcomp-filter package, or with --host a host's settings
# package, tempcomp-filter-HOST, from tools/hosts/HOST: its filter settings
# and its chrony.conf tempcomp line; or with --holdover test 3's
# tempcomp-holdover.
#
# Usage: tools/build_deb.sh [--host HOST | --holdover] [OUTPUT_DIRECTORY]
#        (default dist)

set -euo pipefail

readonly VERSION=0.1.8
TOOLS=$(cd "$(dirname "$0")" && pwd)
readonly TOOLS
readonly HOST_SETTINGS=/etc/tempcomp-filter.d/host.conf
readonly CHRONY_CONF=/etc/chrony/conf.d/tempcomp-filter.conf
readonly HOLDOVER_SETTINGS=/etc/tempcomp-holdover.conf

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

holdover() {
  install -D -m 755 "${TOOLS}/tempcomp-holdover.sh" \
    "${root}/usr/sbin/tempcomp-holdover"
  install -D -m 644 "${TOOLS}/tempcomp-holdover.service" \
    "${root}/lib/systemd/system/tempcomp-holdover.service"
  install -D -m 644 "${TOOLS}/tempcomp-holdover.timer" \
    "${root}/lib/systemd/system/tempcomp-holdover.timer"
  install -D -m 644 "${TOOLS}/tempcomp-holdover.conf" \
    "${root}${HOLDOVER_SETTINGS}"
  control "${TOOLS}/deb/holdover/control" tempcomp-holdover
  echo "${HOLDOVER_SETTINGS}" > "${root}/DEBIAN/conffiles"
  install -m 755 "${TOOLS}/deb/holdover/postinst" \
    "${TOOLS}/deb/holdover/prerm" "${TOOLS}/deb/holdover/postrm" \
    "${root}/DEBIAN/"
}

main() {
  local name=tempcomp-filter host_name="" build=filter
  if [[ ${1:-} == --host ]]; then
    host_name=$2
    name="tempcomp-filter-${host_name}"
    build=host
    shift 2
  elif [[ ${1:-} == --holdover ]]; then
    name=tempcomp-holdover
    build=holdover
    shift
  fi
  local output=${1:-dist}
  # Global: the EXIT trap runs after main has returned.
  root=$(mktemp -d)
  trap 'rm -rf "${root}"' EXIT
  # mktemp's 0700 would be the package's "./".
  chmod 755 "${root}"
  install -d "${root}/DEBIAN"
  case "${build}" in
    host) host "${host_name}" ;;
    holdover) holdover ;;
    *) filter ;;
  esac
  install -D -m 644 "${TOOLS}/../LICENSE" "${root}/usr/share/doc/${name}/copyright"
  mkdir -p "${output}"
  dpkg-deb --root-owner-group --build "${root}" \
    "${output}/${name}_${VERSION}_all.deb"
}

main "$@"
