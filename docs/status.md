# Status

Where the work stands, for whoever picks it up.  The design is
[design.md](design.md); this is the state of the hosts and what is
next.

## Done

- `tools/chrony_tempcomp.py` fixed: it fits the clock's own frequency
  (tracking.log's frequency with the compensation in force at each
  clock update, tempcomp.log's last reading at or before it, taken out)
  and writes the table against the drift (`comp = fref - own`), per
  chrony 4.6's `local.c`.  Before, a table had the drift's own sign
  and doubled it.  Pairing by the reading in force instead of
  interpolating is what the source says, but did not help the fit: on
  draws's logs of 2026-11-01 to 11-06, a day's held-out residual went
  from 0.0652 to 0.0678 ppm.
- The study (`study/`) and four hosts' readings (`data/`), from
  chronymon.
- chronymon (github.com/charlieh0tel/chronymon) fits the lag on its
  Temperature page by blocked cross-validation, against chronyd's
  frequency; it only observes.  Its owner has decided to drop that fit
  (not yet released).

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
- apu-0's PPS is the u-blox 1PPS, stamped by the NIC:
  `refclock PHC /dev/ptp2:extpps:pin=0 ... poll 2`.  `/dev/ptp2` is
  group `_chrony`, so its login can open it.  Kernel 6.12, clocksource
  `tsc`, Python 3.13.
- apu-0's u-blox lost its fix on 2026-11-06, its first `fix_lost` in
  chronymond's journal since 2026-10-25: 22:35:11 to 22:36:23 UTC and
  00:17:29 to 00:54:04 UTC (the 7th).  Without a fix the raw clock read 0.83
  ppm higher against its PPS, which came back with a 1.776 ms step,
  so such a span is not the crystal.  chronyd stopped using the PPS: no
  update from 00:17:44 to 00:48:52, when it took NTP at an offset of
  5.5 us.  The cause was other work on that GPS, not the host.  draws's journal has no such lines,
  and its `tracking.log` stayed on GPS throughout.
- Each host runs chronymon on port 8323; `study/fetch.py` reads it.
- Recorders still run on apu-0 and draws, in `~/tempcomp-run/`; apu-0's
  stops under 64 MB free, and that disk lasts about 8 days at its present
  loss.  A reboot or apu-0's coming SSD swap ends a recorder: start a new
  file after, and copy apu-0's off before the swap.  draws rebooted at
  17:56 UTC on 2026-11-08, ending its first file after 50 h (archived
  whole); its second started at 18:13 UTC, no load step.  Its
  `tempcomp` line changed at 17:55 UTC, just before, to chronymon's
  quadratic fit of the day before (T0 54514, 0, 1.834e-4, -6.856e-9): within
  about 0.05 ppm of the old line over its range.  Raw frequency is
  untouched; chronyd's readings with compensation on split there.
- draws stalls as a whole host, about 400 times a day, at about 5% CPU:
  the recorder reads PPS edges up to 1 s late or loses them (957 of
  179259 over 50 h).  57 of chronymond's 62 missed polls of chronyd in
  that span (no reply in 3 s) fall on one, against 5.5% by chance.
  Likely writes to its 8 GB SD card; the misses rose from 5 a day to
  about 25 once the recorder ran, so it may add to them.  Edges are
  stamped by the kernel, so a stall loses samples without skewing them.  The SSD swap
  waits on the part.
- Raw recordings are kept out of the repo, in
  `~/data/chrony-tempcomp/` on the owner's machine, with their logs, the
  room's database and `SHA256SUMS`; `data/*-cycles.npz` is their bins to
  04:14 UTC on 2026-11-07.

## Working rules

- Ask before ssh to a host.  Changes on a host (chrony.conf, points
  files, services) are the owner's to make: give the exact line or
  command; sudo needs the owner's password.
- Send chronyd only read-only control requests.
- Ask before cloning, building or installing anything.

## Next

1. Find why draws fits about 10 min in quiet running when its crystal
   lags 0.75 min under load (design, "Held-out days").  The 7 min was a
   blend of that and the 0 that compensation forces; the room's stick
   does not close the gap, and neither the sensor's step nor chronyd's
   window explains it (design, "The 10 min").  Next: about 5 days of raw frequency from the
   recorders now running, judged on held-out days; one day would only be
   a trend.  Repeat on chronos once it is ready.  tare has no refclock
   (its `pps0` is a NIC's PTP device with no edges).
   `pps_raw.py --phc` works on apu-0: 2 minutes on 2026-11-06
   (`data/apu-0-pps-check.csv`) gave -22.711 ppm raw against chronyd's
   own -22.708, edge deviation 93 ns against draws's 919 ns, and
   chronyd's PPS samples stayed at 75 a minute.  The PHC free-runs 22.8 s
   behind, within 0.17 ppm of the raw clock over those 2 minutes; whether
   they share an oscillator is unmeasured.
2. Then revise the design's lags and the chrony proposal's guidance to
   what that shows.
3. Phase 1's filter is written and packaged (`tools/build_deb.sh`), with
   draws's settings and `tempcomp` line at a 5 min `tau` in
   `tools/hosts/draws`, not installed.  Then on one host, for before-and-after
   offset and holdover numbers, and the chrony-dev question.
