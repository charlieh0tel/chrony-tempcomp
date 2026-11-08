#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Phase 1 of docs/design.md: a fixed first-order filter between a sensor and
# chronyd's tempcomp.  Reads SENSOR every PERIOD seconds and writes the
# filtered value, in the sensor's own units, to OUTPUT, for a chrony.conf
# line `tempcomp OUTPUT ...`.
#
# Usage:
#   tempcomp-filter run      filter until stopped (the systemd unit)
#   tempcomp-filter check    exit 1 unless configured (the unit's condition)
#   tempcomp-filter off      write OFF_VALUE to OUTPUT: no compensation
#   tempcomp-filter step F READING DT TAU
#                            print one filter step, for the tests
#
# Settings, from DEFAULTS (default /etc/default/tempcomp-filter) or the
# environment:
#   SENSOR     the sensor file, e.g. /sys/class/hwmon/hwmon0/temp1_input
#   TAU        time constant, seconds; 0 passes the reading through
#   PERIOD     seconds between readings (default 5)
#   OUTPUT     where chronyd reads it (default /run/tempcomp-filter/temp)
#   OFF_VALUE  the tempcomp line's T0 when its k0 is 0: no compensation
#   GAP_TAUS   restart from the reading after a gap of this many TAU
#              (default 10)
#   STATS_INTERVAL  seconds between summaries in the log (default 900)
#
# Logs its start, a restart from the reading after a gap, a sensor's
# failure and recovery, and every STATS_INTERVAL the readings taken and
# failed, the sensor's range and last reading, and the filtered value.
#
# A stop, a restart or a crash leaves the last value, a sane temperature;
# a restart carries on from the saved state.  Only `off` (the package runs it
# on removal) turns compensation off.

set -euo pipefail

readonly DEFAULTS=${DEFAULTS:-/etc/default/tempcomp-filter}
if [[ -r "${DEFAULTS}" ]]; then
  # shellcheck source=/dev/null
  source "${DEFAULTS}"
fi
readonly PERIOD=${PERIOD:-5}
readonly OUTPUT=${OUTPUT:-/run/tempcomp-filter/temp}
readonly GAP_TAUS=${GAP_TAUS:-10}
readonly STATS_INTERVAL=${STATS_INTERVAL:-900}
# The filter's value and the uptime it was taken at, kept across restarts.
readonly STATE="${OUTPUT}.state"

log() {
  echo "tempcomp-filter: $*"
}

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

# Prints the seconds from uptime $1 to uptime $2.
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

check() {
  local name
  for name in SENSOR TAU OFF_VALUE; do
    if [[ -z "${!name:-}" ]]; then
      err "set ${name} in ${DEFAULTS}"
      return 1
    fi
  done
  if [[ ! -r "${SENSOR}" ]]; then
    err "cannot read ${SENSOR}"
    return 1
  fi
}

run() {
  check
  local f="" taken="" reading now gap since failing=""
  # Since the last summary: readings, failures, and the sensor's range.
  local count=0 failed=0 low="" high="" last="" summarized
  if [[ -r "${STATE}" ]]; then
    read -r f taken < "${STATE}"
  fi
  gap=$(awk -v t="${TAU}" -v n="${GAP_TAUS}" 'BEGIN { print t * n }')
  summarized=$(uptime_seconds)
  if [[ -n "${f}" ]]; then
    log "start: ${SENSOR}, tau ${TAU} s; carrying on from ${f}"
  else
    log "start: ${SENSOR}, tau ${TAU} s; from the first reading"
  fi
  while true; do
    now=$(uptime_seconds)
    if ! read -r reading < "${SENSOR}"; then
      failed=$((failed + 1))
      if [[ -z "${failing}" ]]; then
        err "cannot read ${SENSOR}; keeping ${f:-nothing}"
        failing=${now}
      fi
    else
      if [[ -n "${failing}" ]]; then
        log "${SENSOR} read again after $(elapsed "${failing}" "${now}") s"
        failing=""
      fi
      count=$((count + 1))
      last=${reading}
      if [[ -z "${low}" ]] || is_longer "${low}" "${reading}"; then
        low=${reading}
      fi
      if [[ -z "${high}" ]] || is_longer "${reading}" "${high}"; then
        high=${reading}
      fi
      if [[ -z "${f}" ]]; then
        f=$(step 0 "${reading}" 0 0)
      else
        since=$(elapsed "${taken}" "${now}")
        if is_longer "${since}" "${gap}"; then
          log "restarting from the reading, ${reading}, after ${since} s"
          f=$(step 0 "${reading}" 0 0)
        else
          f=$(step "${f}" "${reading}" "${since}" "${TAU}")
        fi
      fi
      taken=${now}
      write_atomically "${f}" "${OUTPUT}"
      write_atomically "${f} ${taken}" "${STATE}"
    fi
    since=$(elapsed "${summarized}" "${now}")
    if ! is_longer "${STATS_INTERVAL}" "${since}"; then
      log "last ${since} s: ${count} readings, ${failed} failed;" \
        "sensor ${low:-none} to ${high:-none}, last ${last:-none};" \
        "filtered ${f:-none}"
      count=0 failed=0 low="" high="" last="" summarized=${now}
    fi
    ping_watchdog
    sleep "${PERIOD}"
  done
}

off() {
  : "${OFF_VALUE:?set OFF_VALUE in ${DEFAULTS}}"
  write_atomically "${OFF_VALUE}" "${OUTPUT}"
}

main() {
  case "${1:-}" in
    run) run ;;
    check) check ;;
    off) off ;;
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
