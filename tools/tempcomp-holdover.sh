#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Test 3's holdover (docs/status.md): from START to END, on a cycle of
# CYCLE seconds, chronyd keeps its sources for the first LOCKED seconds
# and holds over for the rest, every source it lists made noselect.
#
# Usage: tempcomp-holdover tick|restore|check
#   tick     set the state the clock calls for (the timer, each minute)
#   restore  give back noselect to only the sources this made noselect
#   check    print the settings and the state now
#
# Settings, in /etc/tempcomp-holdover.conf (Unix seconds):
#   START   the first cycle's start
#   END     the end: from then on, sources are left as configured
#   CYCLE   seconds a cycle
#   LOCKED  seconds of each cycle on the sources, before holding over

set -euo pipefail

readonly SETTINGS=${TEMPCOMP_HOLDOVER_SETTINGS:-/etc/tempcomp-holdover.conf}
# Cleared at boot, as chronyd's runtime selection options are.
readonly STATE_DIR=${STATE_DIRECTORY:-/run/tempcomp-holdover}
# One source a line, as chronyc names it: those this made noselect.
readonly CHANGED=${STATE_DIR}/changed
readonly CHRONYC=${CHRONYC:-chronyc}

log() {
  echo "tempcomp-holdover: $*" >&2
}

load() {
  if [[ ! -r "${SETTINGS}" ]]; then
    log "no ${SETTINGS}"
    exit 1
  fi
  # shellcheck source=/dev/null
  . "${SETTINGS}"
  local name
  for name in START END CYCLE LOCKED; do
    if [[ ! "${!name:-}" =~ ^[0-9]+$ ]]; then
      log "${name} must be a whole number of seconds"
      exit 1
    fi
  done
  if ((LOCKED >= CYCLE)); then
    log "LOCKED must be less than CYCLE"
    exit 1
  fi
}

# True while second $1 is in a cycle's holdover.
holding_over() {
  (($1 >= START && $1 < END && ($1 - START) % CYCLE >= LOCKED))
}

# Makes every source chronyd lists, and that is not noselect, noselect.
hold() {
  local name noselect added=0
  # -c (chrony 4.6): state,name,auth, then the configured options a field
  # each, noselect (N or -) first.
  while IFS=, read -r _ name _ noselect _; do
    [[ "${noselect}" == N ]] && continue
    if ! "${CHRONYC}" selectopts "${name}" +noselect > /dev/null; then
      log "could not make ${name} noselect"
      continue
    fi
    echo "${name}" >> "${CHANGED}"
    log "noselect ${name}"
    added=1
  done < <("${CHRONYC}" -c -n selectdata -a)
  if ((added)); then
    "${CHRONYC}" reselect > /dev/null
  fi
}

restore() {
  [[ -s "${CHANGED}" ]] || return 0
  local name
  while read -r name; do
    # A pool's source may have gone since.
    if "${CHRONYC}" selectopts "${name}" -noselect > /dev/null; then
      log "selectable ${name}"
    else
      log "could not give back ${name}"
    fi
  done < <(sort -u "${CHANGED}")
  rm -f "${CHANGED}"
  "${CHRONYC}" reselect > /dev/null
}

main() {
  case "${1:-}" in
    tick)
      load
      mkdir -p "${STATE_DIR}"
      if holding_over "$(date +%s)"; then
        hold
      else
        restore
      fi
      ;;
    restore)
      restore
      ;;
    check)
      load
      local now
      now=$(date +%s)
      echo "START=${START} END=${END} CYCLE=${CYCLE} LOCKED=${LOCKED}"
      if holding_over "${now}"; then
        echo "holding over"
      else
        echo "on sources"
      fi
      ;;
    *)
      echo "usage: $0 tick|restore|check" >&2
      exit 2
      ;;
  esac
}

main "$@"
