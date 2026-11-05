# Status

Where the work stands, for whoever picks it up.  The design is
[design.md](design.md); this is the state of the hosts and what is
next.

## Done

- `tools/chrony_tempcomp.py` fixed: it fits the clock's own frequency
  (tracking.log's frequency less tempcomp.log's compensation) and
  writes the table against the drift (`comp = fref - own`), per
  chrony 4.6's `local.c`.  Before, a table had the drift's own sign
  and doubled it.
- The study (`study/`) and four hosts' readings (`data/`), from
  chronymon.
- chronymon (github.com/charlieh0tel/chronymon) fits the lag on its
  Temperature page by blocked cross-validation, against chronyd's
  frequency; it only observes.

## Hosts

| Host | Reference | tempcomp sensor | Step | tempcomp now |
|---|---|---|---|---|
| apu-0 | PPS (PHC `/dev/ptp2`) | `/sys/devices/virtual/thermal/thermal_zone0/hwmon0/temp1_input`, 15 s | 0.1 °C | quadratic, below |
| chronos | PPS | `/sys/class/thermal/thermal_zone0/temp`, 32 s | 0.454 °C | `0 0 0 0` (logging only) |
| draws | PPS (`/dev/pps0`), gpsd | `/sys/class/hwmon/hwmon0/temp1_input`, 15 s | 0.538 °C | a line fitted by chronymon |
| tare | NTP only | coretemp `temp1_input`, 30 s | 1 °C | `0 0 0 0` (logging only) |

- apu-0's line since 2026-11-05 05:41 UTC, refitted with the fixed
  script from its logs of 2026-10-31 to 11-05 (58.7 to 71.7 °C,
  held-out residual 0.045 ppm):

      tempcomp /sys/devices/virtual/thermal/thermal_zone0/hwmon0/temp1_input 15 63223 0 0.0001283 -6.25e-09

  It replaced a point table with the wrong sign.  Outside 58.7 to
  71.7 °C it is extrapolation; the 80.5 to 82 °C anomaly once seen on
  this box is not in it.  Check after a day that the frequency's
  daily swing has fallen well below the clock's own (about 1.4 ppm).
- apu-0 over ssh: `apu-0.murgatroid.com`; the short name resolves to
  a Tailscale address that hangs.  The login is in group `_chrony`,
  so `/var/log/chrony` is readable without sudo.
- Each host runs chronymon on port 8323; `study/fetch.py` reads it.

## Working rules

- Ask before ssh to a host.  Changes on a host (chrony.conf, points
  files, services) are the owner's to make: give the exact line or
  command; sudo needs the owner's password.
- Send chronyd only read-only control requests.
- Ask before cloning, building or installing anything.

## Next

1. Find why draws's readings fit a 7 min lag when its crystal lags
   about 1 min under a load step (design, "Why the readings fit a longer
   lag"): smoothing is about half and a slower path for load's heat is
   ruled out, leaving chronyd's window in quiet running or the room's
   path.  Next: days of `study/pps_raw.py` in quiet running on draws,
   with `chronyc sourcestats`.  Repeat on chronos once it is ready.
   tare has no refclock (its
   `pps0` is a NIC's PTP device with no edges), and apu-0's PPS is on a
   PHC pin, which the recorder does not read.
2. Then revise the design's lags and the chrony proposal's guidance to
   what that shows.
3. Then phase 1 (a fixed filter) on one host, for before-and-after
   offset and holdover numbers, and the chrony-dev question.
