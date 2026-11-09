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
  apu-0 rebooted at 20:38 UTC on 2026-11-08, ending its first file
  (`pps-20261106T155019.csv`, 2.2 days, about 21 MB a day); its second
  started at 23:19 UTC, with 553 MB free.
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
3. Phase 1's filter is packaged (`tools/build_deb.sh`) and runs on draws
   at a 5 min `tau` (`tools/hosts/draws`): `tempcomp-filter` 0.1.1, read
   by chronyd from 2026-11-08 20:49:09 UTC.  chronyd ran uncompensated
   from its restart at 20:45:09 until then.  0.1.2 (logging) from 21:07:09;
   its upgrade held the compensation still from 21:05:53, as the package's
   blank settings replaced draws's.  Before: 2026-11-01 to
   11-08 on a line within 0.05 ppm of this one, unfiltered.  Whether it
   beats the unfiltered sensor, in three tests:
   1. Replay on the raw recording (`study/filter_gain.py`;
      `data/filter-gain-draws.txt`).  On draws's 1.9 quiet days the
      filter at 300 s leaves 0.0256 ppm RMS against 0.0412 unfiltered,
      better on each day; holdover error 44 against 65 us after 1 h, 248
      against 325 after 4 h.  180 s scores 0.0250, 600 s 0.0311.  The line
      was fitted to the unfiltered sensor, which favors it.  On apu-0's
      2.0 days (`data/filter-gain-apu-0.txt`; its 0.1 degree sensor has
      little step to smooth) 0.0277 at 180 s and 0.0276 at 300 s against
      0.0316, holdover even at 1 and 4 h; `TAU` 180 there
      (`tempcomp-filter-apu-0`), read by chronyd from its restart at
      01:16:44 UTC on 2026-11-09 (0.1.3, which writes into `/run/chrony`,
      as chronyd's AppArmor profile there reads only that and sysfs).
      Fitting the line for each `tau` (`--fit`, with the second
      recordings to 21:18 UTC on 2026-11-09; two whole days held out on
      each host, each scored on the fit to the other) keeps the answer:
      draws best at 300 s, 0.0239 against 0.0402 unfiltered (41% less),
      holdover 43 against 59 us after 1 h, 212 against 313 after 4 h;
      apu-0 best at 450 s, 0.0397 against 0.0455 (13%), holdover a little
      worse at 1 and 4 h (66 against 64 us, 566 against 539) and better
      at 24 h.  The fitted lines barely move with `tau` (k1 within 1%), so
      fitting to the unfiltered sensor did not bias the choice; both sit
      near the installed lines (draws's k2 -6.2e-9 against -6.86e-9,
      apu-0's k1 1.33e-4 and k2 -8.2e-9 against 1.28e-4 and -6.25e-9).
      apu-0's day since its reboot fits worse at every `tau` (0.050
      against 0.041 unfiltered), cause unknown.  Repeat on more days:
      the crystal's own frequency each minute against the installed line
      on the sensor as chronyd reads it (an instant reading every 15 s)
      and on the filter's output (every 5 s through `tau`), same minutes;
      RMS of what is left, day by day, and the time error it would build
      over 1, 4 and 24 h of holdover.  Free of chronyd; tries other `tau`
      and two speeds without touching the host.
   2. On the host: chronyd's frequency, less the compensation, wanders
      less when the compensation fits.  Its spread over 15 min to 4 h
      (`tracking.log`, `tempcomp.log`), the filter at `TAU` 300 and
      compensation off alternated daily for about a week, so weather and
      load even out.  Filtered against unfiltered is largely answered (the
      tenfold below), so off is the comparison that matters.  A script's
      cron job on draws ran `TAU` 0 from 23:48 to 23:55 UTC on 2026-11-08
      and was removed, as changes to a host go in packages.  0.1.3's
      `ALTERNATE_TAU=0` alternated with unfiltered; 0.1.5's
      `ALTERNATE_TAU=off`, in `tempcomp-filter-draws`, writes `OFF_VALUE`
      on odd UTC days since the epoch (the first 2026-11-10), filtering on
      underneath, logged at each switch: on draws from 21:22 UTC on
      2026-11-09, and on apu-0 (`tempcomp-filter-apu-0`, `TAU` 180) from
      21:23 UTC, the same days.  Each switch is a step in the
      compensation (as much as the line gives at the day's temperature),
      which chronyd must absorb; judge from an hour after it.
   3. Holdover: the reference cut for a few hours, filter on and off, the
      time error against the recorder's PPS.  The before-and-after
      numbers, and the chrony-dev question.
   chronyd itself steadied about tenfold on each host when it began
   reading the filter (`tracking.log`, 2.5 h before and after): apu-0's
   frequency step SD 8.1 to 0.7 ppb, skew 17 to 2 ppb, offset RMS 127 to
   10 ns; draws's 25.8 to 1.8 ppb, 30 to 3 ppb, 5.6 us to 188 ns.  draws's
   0.1.3 restart, filtered on both sides, changed nothing (1.9 to 1.7 ppb).
   Each jitter of an unfiltered reading is a frequency step chronyd
   chases (0.013 ppm per 0.1 degree on apu-0); the replay, scoring the fit
   to the crystal each minute, does not see that; its step column does:
   the compensation's RMS change between chronyd's readings, 93 ppb
   unfiltered against 2.5 at 300 s on draws, 34 against 1.5 at 180 s on
   apu-0.  Against compensation
   off, which adds no such noise, test 2 and holdover still decide.
   0.1.3 on both hosts (draws's chrony restarted 01:17:20 UTC on
   2026-11-09, `TAU` 300 that day): output in `/run/chrony`, run as
   `_chrony` after chrony; settings from a per-host package in
   `/etc/tempcomp-filter.d`, `/etc/default/tempcomp-filter` shipped
   commented out; `ALTERNATE_TAU`; `TAU` 0 no longer logs a gap restart
   each reading.  0.1.5 (on draws from 21:22 UTC on 2026-11-09, apu-0 from 21:23) adds `ALTERNATE_TAU=off`
   and `ALTERNATE_DAYS`, and fixes a host package's first install (0.1.4,
   never installed): it left the filter stopped until chrony restarted.  The script is past the limit of shell (floats and stats
   through `awk`, several processes a reading): its next change moves it
   to Python, or to Rust with phase 2.
4. Integrate with chronymon.  chronymond reads the `tempcomp` line's
   sensor (`crates/chronymond/src/config.rs`, comments skipped), so with
   the filter it sees `/run/chrony/tempcomp-filter` and no more: not that
   the reading is filtered, nor its `tau`.  Its Temperature page, lag and
   refits then work on a sensor already `tau` behind, and the line it
   writes must name the filter's file.  To settle: how chronymond learns
   of the filter (its settings file, or a marker agreed with it), whether
   it fits against the raw sensor or the filtered one, and who owns the
   conf.d file.  chrony applies the line; the filter only delays the
   sensor (design, phase 1 and 2).
