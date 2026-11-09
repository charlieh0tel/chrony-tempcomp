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
#   tempcomp-filter tau [UNIX_SECONDS]
#                            print the time constant in force then (now)
#
# Settings, from DEFAULTS (default /etc/default/tempcomp-filter), then each
# HOST_SETTINGS/*.conf (default /etc/tempcomp-filter.d, where a host's
# package puts them), or the environment:
#   SENSOR     the sensor file, e.g. /sys/class/hwmon/hwmon0/temp1_input
#   TAU        time constant, seconds; 0 passes the reading through
#              (default 300)
#   ALTERNATE_TAU  if set, the time constant on ALTERNATE_DAYS, for
#              comparing two (docs/status.md, test 2); `off` writes OFF_VALUE
#              those days, the filter running on at TAU
#   ALTERNATE_DAYS  `odd` or `even` UTC days since the epoch (default odd)
#   PERIOD     seconds between readings (default 5)
#   OUTPUT     where chronyd reads it (default /run/chrony/tempcomp-filter,
#              which Debian's AppArmor profile for chronyd lets it read)
#   STATE      the filter's state (default /run/tempcomp-filter/state)
#   OFF_VALUE  the tempcomp line's T0 when its k0 is 0: no compensation
#   GAP_TAUS   restart from the reading after a gap of this many TAU
#              (default 10)
#   STATS_INTERVAL  seconds between summaries in the log (default 900)
#
# Logs its start, a change of time constant, a restart from the reading
# after a gap, a sensor's or the output's failure and recovery, and every STATS_INTERVAL the readings taken and
# failed, the sensor's range and last reading, the filtered value and the
# value written.
#
# A stop, a restart or a crash leaves the last value, a sane temperature;
# a restart carries on from the saved state.  Only `off` (the package runs it
# on removal) turns compensation off.

set -euo pipefail

readonly DEFAULTS=${DEFAULTS:-/etc/default/tempcomp-filter}
readonly HOST_SETTINGS=${HOST_SETTINGS:-/etc/tempcomp-filter.d}
for settings in "${DEFAULTS}" "${HOST_SETTINGS}"/*.conf; do
  if [[ -r "${settings}" ]]; then
    # shellcheck source=/dev/null
    source "${settings}"
  fi
done
readonly TAU=${TAU:-300}
readonly PERIOD=${PERIOD:-5}
readonly OUTPUT=${OUTPUT:-/run/chrony/tempcomp-filter}
readonly GAP_TAUS=${GAP_TAUS:-10}
readonly STATS_INTERVAL=${STATS_INTERVAL:-900}
# The filter's value and the uptime it was taken at, kept across restarts.
readonly STATE=${STATE:-/run/tempcomp-filter/state}
readonly ALTERNATE_DAYS=${ALTERNATE_DAYS:-odd}
readonly SECONDS_PER_DAY=86400

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
  printf '%s\n' "${value}" > "${path}.tmp" && mv -f "${path}.tmp" "${path}"
}

# Prints the time constant in force at Unix time $1.
tau_at() {
  local parity=1
  if [[ "${ALTERNATE_DAYS}" == even ]]; then
    parity=0
  fi
  if [[ -n "${ALTERNATE_TAU:-}" ]] && (($1 / SECONDS_PER_DAY % 2 == parity)); then
    echo "${ALTERNATE_TAU}"
  else
    echo "${TAU}"
  fi
}

# Describes time constant $1 for the log.
describe() {
  if [[ "$1" == off ]]; then
    echo "off (writing ${OFF_VALUE}, filtering at tau ${TAU} s)"
  else
    echo "tau $1 s"
  fi
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
  for name in SENSOR OFF_VALUE; do
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
  local f="" taken="" reading now gap since failing="" unwritten=""
  local tau filter_tau written
  # Since the last summary: readings, failures, and the sensor's range.
  local count=0 failed=0 low="" high="" last="" summarized
  if [[ -r "${STATE}" ]]; then
    read -r f taken < "${STATE}"
  fi
  tau=$(tau_at "${EPOCHSECONDS}")
  summarized=$(uptime_seconds)
  if [[ -n "${f}" ]]; then
    log "start: ${SENSOR}, $(describe "${tau}"); carrying on from ${f}"
  else
    log "start: ${SENSOR}, $(describe "${tau}"); from the first reading"
  fi
  while true; do
    now=$(uptime_seconds)
    if [[ $(tau_at "${EPOCHSECONDS}") != "${tau}" ]]; then
      tau=$(tau_at "${EPOCHSECONDS}")
      log "$(describe "${tau}") from today (UTC)"
    fi
    filter_tau=${tau}
    if [[ "${tau}" == off ]]; then
      filter_tau=${TAU}
    fi
    gap=$(awk -v t="${filter_tau}" -v n="${GAP_TAUS}" 'BEGIN { print t * n }')
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
        if is_longer "${filter_tau}" 0 && is_longer "${since}" "${gap}"; then
          log "restarting from the reading, ${reading}, after ${since} s"
          f=$(step 0 "${reading}" 0 0)
        else
          f=$(step "${f}" "${reading}" "${since}" "${filter_tau}")
        fi
      fi
      taken=${now}
      written=${f}
      if [[ "${tau}" == off ]]; then
        written=${OFF_VALUE}
      fi
      # chrony's runtime directory goes while chrony is stopped.
      if write_atomically "${written}" "${OUTPUT}" 2> /dev/null; then
        if [[ -n "${unwritten}" ]]; then
          log "${OUTPUT} written again after $(elapsed "${unwritten}" "${now}") s"
          unwritten=""
        fi
      elif [[ -z "${unwritten}" ]]; then
        err "cannot write ${OUTPUT}; retrying"
        unwritten=${now}
      fi
      write_atomically "${f} ${taken}" "${STATE}"
    fi
    since=$(elapsed "${summarized}" "${now}")
    if ! is_longer "${STATS_INTERVAL}" "${since}"; then
      log "last ${since} s: ${count} readings, ${failed} failed;" \
        "sensor ${low:-none} to ${high:-none}, last ${last:-none};" \
        "filtered ${f:-none}, written ${written:-none}"
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
    tau) tau_at "${2:-${EPOCHSECONDS}}" ;;
    *)
      sed -n '9,16s/^# //p' "$0" >&2
      exit 2
      ;;
  esac
}

main "$@"
