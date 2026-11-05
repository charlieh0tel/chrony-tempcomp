#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install a tempcomp points file into chrony and restart it, rolling back on failure.
# Usage: sudo ./install_points.sh POINTS [SENSOR] [INTERVAL]
#   SENSOR defaults to the acpitz thermal zone; INTERVAL to 15 s.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ $# -ge 1 ]] || { sed -n '4,5s/^# //p' "$0" >&2; exit 2; }

POINTS=$1
SENSOR=${2:-}
INTERVAL=${3:-15}
C=/etc/chrony

if [[ -z $SENSOR ]]; then
    for z in /sys/class/thermal/thermal_zone*; do
        [[ $(cat "$z/type") == acpitz ]] && SENSOR=$z/temp && break
    done
    [[ -n $SENSOR ]] || { echo "no acpitz thermal zone; name a SENSOR" >&2; exit 1; }
fi
cat "$SENSOR" >/dev/null

# chronyd exits on any line that is not "<temp> <comp>"
if grep -qvE '^-?[0-9.]+ -?[0-9.]+$' "$POINTS"; then
    echo "$POINTS has lines chrony cannot parse" >&2; exit 1
fi

BACKUP=/root/chrony-backup-$(date +%Y%m%d-%H%M%S).tar.gz
tar -czf "$BACKUP" -C /etc chrony
echo "backup: $BACKUP"
rollback() {
    echo "!! failed, restoring $BACKUP" >&2
    rm -rf "$C" && tar -xzf "$BACKUP" -C /etc
    systemctl restart chrony
    exit 1
}
trap rollback ERR

install -m 644 "$POINTS" "$C/tempcomp.points"
echo "tempcomp $SENSOR $INTERVAL $C/tempcomp.points" > "$C/conf.d/tempcomp.conf"

chronyd -p -f "$C/chrony.conf" | grep tempcomp
systemctl restart chrony
sleep $((INTERVAL + 5))
systemctl is-active --quiet chrony
trap - ERR

tail -3 /var/log/chrony/tempcomp.log
chronyc tracking | grep -E 'Reference|Frequency|Residual|Skew'
