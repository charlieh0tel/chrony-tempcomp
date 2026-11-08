#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Phase 1 of docs/design.md: a fixed first-order filter between a sensor and
# chronyd's tempcomp.  Reads SENSOR every PERIOD seconds and writes the
# filtered value, in the sensor's own units, to OUTPUT, for a chrony.conf
# line `tempcomp OUTPUT ...`.
#
# Usage:
#   tempcomp-filter.sh run         filter until stopped (the systemd unit)
#   tempcomp-filter.sh stop        write OFF_VALUE to OUTPUT
#   tempcomp-filter.sh step F READING DT TAU
#                                  print one filter step, for the tests
#
# Settings, from the environment (the unit reads /etc/default/tempcomp-filter):
#   SENSOR     the sensor file, e.g. /sys/class/hwmon/hwmon0/temp1_input
#   TAU        time constant, seconds
#   PERIOD     seconds between readings (default 5)
#   OUTPUT     where chronyd reads it (default /run/tempcomp-filter/temp)
#   OFF_VALUE  written on stop: the line's T0 when its k0 is 0, which is no
#              compensation, as with tempcomp off
#   GAP_TAUS   restart from the reading after a gap of this many TAU
#              (default 10)

set -euo pipefail

readonly PERIOD=${PERIOD:-5}
readonly OUTPUT=${OUTPUT:-/run/tempcomp-filter/temp}
readonly GAP_TAUS=${GAP_TAUS:-10}
# The filter's value and the uptime it was taken at, kept across restarts.
readonly STATE="${OUTPUT}.state"

err() {
  echo "tempcomp-filter: $*" >&2
}

# Prints f moved toward the reading by one step of DT seconds.
step() {
  local f=$1 reading=$2 dt=$3 tau=$4
  awk -v f="${f}" -v r="${reading}" -v dt="${dt}" -v tau="${tau}" 'BEGIN {
    if (tau <= 0) { printf "%.1f\n", r; exit }
    printf "%.1f\n", f + (1 - exp(-dt / tau)) * (r - f)
  }'
}

# Writes $1 to $2 so a reader sees the old file or the new, never part.
write_atomically() {
  local value=$1 path=$2
  printf '%s\n' "${value}" > "${path}.tmp"
  mv -f "${path}.tmp" "${path}"
}

uptime_seconds() {
  local up _
  read -r up _ < /proc/uptime
  echo "${up}"
}

# Prints "seconds since" the uptime $1, now being $2.
elapsed() {
  awk -v taken="$1" -v now="$2" 'BEGIN { printf "%.3f\n", now - taken }'
}

is_longer() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }'
}

ping_watchdog() {
  if [[ -n "${NOTIFY_SOCKET:-}" ]]; then
    systemd-notify WATCHDOG=1
  fi
}

run() {
  : "${SENSOR:?set SENSOR}" "${TAU:?set TAU}" "${OFF_VALUE:?set OFF_VALUE}"
  local f="" taken="" reading now gap
  if [[ -r "${STATE}" ]]; then
    read -r f taken < "${STATE}"
  fi
  gap=$(awk -v t="${TAU}" -v n="${GAP_TAUS}" 'BEGIN { print t * n }')
  # A stop (TERM) turns compensation off.  A crash or the watchdog's ABRT
  # leaves the last value, and a restart carries on from the state.
  trap 'stop; exit 0' TERM
  while true; do
    now=$(uptime_seconds)
    if ! read -r reading < "${SENSOR}"; then
      err "cannot read ${SENSOR}; keeping ${f:-nothing}"
    elif [[ -z "${f}" ]] || is_longer "$(elapsed "${taken}" "${now}")" "${gap}"; then
      f=$(step 0 "${reading}" 0 0)
      taken=${now}
    else
      f=$(step "${f}" "${reading}" "$(elapsed "${taken}" "${now}")" "${TAU}")
      taken=${now}
    fi
    if [[ -n "${f}" ]]; then
      write_atomically "${f}" "${OUTPUT}"
      write_atomically "${f} ${taken}" "${STATE}"
    fi
    ping_watchdog
    sleep "${PERIOD}"
  done
}

stop() {
  : "${OFF_VALUE:?set OFF_VALUE}"
  write_atomically "${OFF_VALUE}" "${OUTPUT}"
}

main() {
  case "${1:-}" in
    run) run ;;
    stop) stop ;;
    step)
      shift
      step "$@"
      ;;
    *)
      sed -n '9,14s/^# //p' "$0" >&2
      exit 2
      ;;
  esac
}

main "$@"
