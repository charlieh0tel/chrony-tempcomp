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
      With the second recordings to 19:00 UTC on 2026-11-10
      (`data/filter-gain-*-all.txt`, `*-fit.txt`), which take in test 2's
      first off day, and a line fitted for each `tau` (`--fit`; three whole
      days held out on each host, each scored on the fit to the others),
      the answer holds: draws best at 300 to 450 s, 0.0246 against 0.0400
      unfiltered (39% less), holdover 49 against 65 us after 1 h, 246
      against 326 after 4 h, 1.7 against 2.4 ms after 24 h; apu-0 best at
      450 s, 0.0393 against 0.0452 (13%), holdover even at 1 and 4 h and
      3.6 against 4.3 ms at 24 h.  No compensation, on the same minutes,
      leaves 0.49 ppm on draws and 0.44 on apu-0, and holdover errors of
      0.3 ms after 1 h, 3.5 to 4.2 ms after 4 h and 38 to 46 ms after 24 h:
      the filtered line cuts them 4 to 6 times at 1 h, 6 to 17 times at
      4 h and 11 to 27 times at 24 h.  The fitted lines barely move with
      `tau` (k1 within 1%), so fitting to the unfiltered sensor did not
      bias the choice; both sit near the installed lines (draws's k2
      -6.2e-9 against -6.86e-9, apu-0's k1 1.32e-4 and k2 -8.4e-9 against
      1.28e-4 and -6.25e-9).  apu-0's days since its reboot fit the
      installed line worse (0.05 to 0.06 ppm against 0.03), cause unknown.
      Repeat on more days:
      the crystal's own frequency each minute against the installed line
      on the sensor as chronyd reads it (an instant reading every 15 s)
      and on the filter's output (every 5 s through `tau`), same minutes;
      RMS of what is left, day by day, and the time error it would build
      over 1, 4 and 24 h of holdover.  Free of chronyd; tries other `tau`
      and two speeds without touching the host.
      To 01:56 UTC on 2026-11-16 (`data/filter-gain-*-1116.txt`; 8 whole
      days on draws, 6 on apu-0, whose recorder its disk guard stopped
      from 03:17 UTC on 11-13 to 01:36 on 11-14, a UPS's log flood having
      filled the disk): draws at 300 s 0.0259 against 0.0387 unfiltered,
      holdover 46 against 56 us after 1 h, 268 against 340 after 4 h, 1.9
      against 2.5 ms after 24 h; apu-0 at 180 s 0.0349 against 0.0400, 59
      against 61 us, 465 against 474, 2.8 against 3.1 ms.  No compensation
      leaves 0.24 to 0.30 ms after 1 h, 3.1 to 3.7 after 4 h, 33 to 38
      after 24 h: 4 to 5 times, 8 to 11 times and 14 to 17 times the
      filtered line's.  apu-0's fitted k2 is now -9.1e-9 against the
      installed -6.25e-9.  Its one day before the reboot fits the
      installed line better (0.026 against 0.051 ppm), every day since
      the refit: so refitted on the days since the reboot alone
      (`data/filter-gain-apu-0-refit.txt`), each held out, k1 1.250e-4 and
      k2 -1.081e-8 about the same T0 leave 0.0284 ppm against the
      installed line's 0.0532 on the same minutes, holdover 60 against 59
      us after 1 h, 379 against 543 after 4 h, 2.6 against 4.4 ms after
      24 h.  0.1.6 carried it and was never installed.  On the 12 days to
      the SSD's replacement on 11-22 (`data/filter-gain-apu-0-refit-1126.txt`)
      the refit, k1 1.288e-4 and k2 -1.046e-8, leaves 0.0383 ppm against
      0.0713, holdover 55 against 55 us after 1 h, 364 against 511 after
      4 h, 2.7 against 4.6 ms after 24 h; 0.1.7 carries it.  The SSD's
      replacement did not move the curve: on the filtered days after it
      (11-23, 11-25) chronyd's own frequency less the compensation sits
      0.019 and 0.021 ppm about the refit, against 0.058 and 0.015 about
      the installed line, as on the days before (0.026 and 0.026 against
      0.052 and 0.014).  What moved at the 11-08 reboot is unknown.
      To 11-24, draws's 10 whole days (`data/filter-gain-draws-1126.txt`,
      its 5 days without a GPS fix, its antenna's feed
      down, left out) at 300 s leave 0.0280 ppm
      against 0.0404 unfiltered, holdover 50 against 62 us after 1 h,
      279 against 347 after 4 h, 2.0 against 2.5 ms after 24 h; its
      fitted line is within 1% of the installed one, and the card's
      replacement moved nothing either (11-25: 0.017 about a refit,
      0.018 about the installed line).  apu-0's 12 days, its line fitted
      for each `tau` on all of them (`data/filter-gain-apu-0-1126.txt`):
      0.0375 at 180 s against 0.0424, 56 against 58 us, 401 against 415,
      2.5 against 2.7 ms.  No compensation leaves 0.26 to 0.27 ms after
      1 h, 3.1 ms after 4 h, 32 to 38 ms after 24 h.
   2. On the host: chronyd's frequency, less the compensation, wanders
      less when the compensation fits.  Its spread over 15 min to 4 h
      (`tracking.log`, `tempcomp.log`), the filter at `TAU` 300 and
      compensation off alternated daily for about a week, so weather and
      load even out.  Filtered against unfiltered is largely answered (the
      steadying below), so off is the comparison that matters.  A script's
      cron job on draws ran `TAU` 0 from 23:48 to 23:55 UTC on 2026-11-08
      and was removed, as changes to a host go in packages.  0.1.3's
      `ALTERNATE_TAU=0` alternated with unfiltered; 0.1.5's
      `ALTERNATE_TAU=off`, in `tempcomp-filter-draws`, writes `OFF_VALUE`
      on odd UTC days since the epoch (the first 2026-11-10), filtering on
      underneath, logged at each switch: on draws from 21:22 UTC on
      2026-11-09, and on apu-0 (`tempcomp-filter-apu-0`, `TAU` 180) from
      21:23 UTC, the same days.  The first off day began on time on
      both: at 00:00 UTC on 2026-11-10 the filter logged the switch and
      chronyd's next reading (00:00:09 on draws, 00:00:03 on apu-0) put
      the compensation to 0, a step of 0.887 ppm on draws and 0.293 on
      apu-0; the filter ran on underneath (60816 against a sensor of
      60148 to 62300 on draws, 65813 against 65200 to 66300 on apu-0),
      both stayed on their reference, and both recorders kept writing.  Each switch is a step in the
      compensation (as much as the line gives at the day's temperature),
      which chronyd must absorb; judge from an hour after it.
      Six whole days to 2026-11-16, each from an hour after its switch
      (`tracking.log`): off on 11-10, 12 and 14, filtered on 11-11, 13
      and 15.  draws's frequency step SD 2.2, 3.3, 2.7 ppb off against 1.9,
      1.7, 2.7 filtered, median offset 56 to 63 ns against 56 to 74: the
      two overlap.  apu-0's 0.48 to 0.56 ppb against 0.57 to 0.66, median
      offset 3.1 to 3.7 ns against 3.8 to 4.6: each filtered day a little
      noisier than the off days either side, the compensation's own motion
      being a change chronyd chases.  While locked, compensation is worth
      little either way; holdover decides.
      Test 2 ran to 11-26: every off day wrote T0 all day and every
      filtered day never did, on both hosts.  draws, on GPS and clear of
      its outage and reboot: off days 2.2 to 6.4 ppb step SD (median 3.2),
      filtered 1.7 to 3.6 (median 2.3); median offset 56 to 75 ns against
      56 to 89.  apu-0: 0.48 to 0.70 ppb against 0.54 to 0.66, median
      offset 2.4 to 3.7 ns against 3.5 to 4.6; the same verdict.
      chronyd took each switch in within 68 to 114 s on draws (10
      switches, 12 to 30 us of offset), 24 to 32 s on apu-0 (6, 0.8 to
      2.3 us) (`data/chronyd-step-*-1126.txt`).  0.1.7 ends test 2:
      `ALTERNATE_TAU` is gone from both host packages.
   3. Holdover: the reference cut for a few hours, filter on and off, the
      time error against the recorder's PPS.  The before-and-after
      numbers, and the chrony-dev question.
   chronyd itself steadied when it began reading the filter
   (`tracking.log`, 2 h ending 10 min before chronyd's restart and 2 h
   starting 30 min after the switch, clear of the install and the
   settling): apu-0's frequency step SD 7.8 to 0.70 ppb, skew 17 to 2 ppb,
   offset RMS 120 to 10 ns; draws's 9.2 to 0.91 ppb, 29 to 2 ppb, 646 to
   140 ns.  (draws's 25.8 ppb and 5.6 us, given before, took in its
   restart at 20:45:09 and 4 min uncompensated.)  draws's 0.1.3 restart,
   filtered on both sides, spans taken the same way: 2.0 to 1.3 ppb, 183
   to 155 ns, about what spans a few hours apart differ by unchanged.
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
