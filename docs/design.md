# chrony-tempcomp: design

chrony-tempcomp fits chrony's `tempcomp` from chrony's own logs
(`tools/chrony_tempcomp.py`, a point table) and is to grow a small daemon
and CLI that feed `tempcomp` a temperature filtered to match how the
crystal actually follows the sensor, and a proposal to chrony for the
same filter built in.  [chronymon](https://github.com/charlieh0tel/chronymon)
measures and charts what this changes; it stays an observer, while
this steers the clock.

## Background

### What chronyd's tempcomp does

Every `interval` seconds chronyd reads one number from the sensor
file, T, computes

    comp = k0 + (T - T0) k1 + (T - T0)^2 k2      (ppm)

and applies it to the clock's frequency at once (`read_timeout`,
`LCL_SetTempComp`, `tempcomp.c`, chrony 4.6; draws runs 4.3, whose
`local.c` is the same and whose point lookup behaves the same).  There is no smoothing:
each reading's compensation replaces the last.  chronyd's own loop
then estimates whatever frequency error remains, from its reference,
at its update interval.

The sign: chrony gives the kernel `freq - comp` and logs
`freq = own + comp`, `freq` being the correction it wants before
compensation and `own` the clock's own (`LCL_SetAbsoluteFrequency`,
`LCL_ReadAbsoluteFrequency`, `local.c`).  A table that holds the
logged frequency steady is `comp = fref - own`, against the drift; one
with the drift's own sign doubles it.  Under an active table the
logged frequency is not the clock's own: a fit adds the logged
compensation back.

### Why reading the sensor directly fails

The crystal's frequency depends on the crystal's temperature, Tc, not
the sensor's.  The sensor is somewhere else (a CPU's die, an SoC, a
board), and heat reaches the crystal through the board's thermal
resistance and capacitance.  To first order the crystal follows the
sensor through a low-pass, time constant tau:

    tau dTc/dt = T - Tc

The sensor also has its own noise, and is discretized (below).

Given the raw reading, tempcomp compensates for T, while the clock
drifts with Tc.  The error it puts into the frequency is

    e(t) = k1 (T(t) - Tc(t))

For changes slower than tau, Tc keeps up and e is small.  For faster
ones, e is nearly the whole change times k1: a load spike that heats a
CPU 30 °C in a minute is compensated in full at the next reading,
while the crystal has barely warmed, then compensated back when the
load ends.  In the frequency domain the error is the high-pass part of
T: |e| / |k1 T| = w tau / sqrt(1 + (w tau)^2).  The sensor's jitter
passes straight through the same way.

A frequency error integrates into offset: e for one update interval
is e x interval of time error before chronyd's loop measures and
corrects it.  On chronos (PPS, 16 s updates, 32 s readings) the raw
sensor's jitter alone is about 0.09 ppm RMS, about 3 us per reading,
twice its present RMS offset.  On tare (NTP, 257 s updates), load
spikes give errors of up to 3 ppm.

The fit suffers too.  Regressing the frequency on the raw temperature
is an errors-in-variables problem: the fast swings and jitter in T
that the crystal never feels dilute the slope toward zero.  On chronos
the raw fit's slope is -0.127 ppm/°C against -0.19 ppm/°C through the lag; a
line fitted on raw readings both under-corrects the slow drift and
injects the fast noise.

### Discretization

Linux reports a sensor in millidegrees Celsius, but each sensor
resolves only its own step, which differs from sensor to sensor.
Measured from an hour of each host's readings (the gaps between
distinct values), with the jump each step makes in the compensation
at the host's fitted slope:

| Host | Sensor | Step | Slope | Jump per step |
|---|---|---|---|---|
| tare | coretemp | 1 °C | about -0.09 ppm/°C | about 0.09 ppm |
| draws | board sensor | 0.538 °C | -0.177 ppm/°C | 0.095 ppm |
| chronos | thermal zone | 0.454 °C | -0.19 ppm/°C | 0.086 ppm |
| apu-0 | SoC | 0.1 °C | -0.131 ppm/°C | 0.013 ppm |

The step matters at every stage:

- **Analysis.**  A reading is the temperature to within a step: an
  error of at least step^2 / 12 in variance (0.29 °C RMS for a 1 °C
  step), but not white noise: while the temperature drifts slowly
  near a step's edge, the reading flips between two levels, a
  square wave the crystal never feels.  It is part of the error in
  the temperature that dilutes a raw fit's slope.  The steps are
  found from the data, the gaps between distinct readings, not
  assumed.
- **Post-processing.**  The lag filter averages the flips, and
  resolves the temperature finer than a step only because the
  temperature wanders across steps (dither); with too short a tau
  the flips pass through.  A Kalman filter's measurement noise is at
  least step^2 / 12.  The filtered value is written with finer
  resolution than the sensor's: chronyd reads the file as a double
  (`fscanf("%lf")`), so fractional millidegrees are kept.
- **Control loop.**  Read raw, every flip is a jump of the
  compensation by the table's last column, applied at once: on
  chronos about 0.09 ppm every time the reading flips, the same size
  as the 0.09 ppm RMS error the raw sensor injects there.  Filtered,
  the compensation moves smoothly.  The program measures the step at
  start and reports it (`status`), and checks that its tau is long
  against how often the reading flips.

### How the lag and the fit are found

The model is the frequency the clock would run at with no
compensation, y = chronyd's frequency less the compensation applied,
against the temperature through some response h:

    y(t) = c + k1 (h * T)(t) + k2 (h * T)(t)^2 + noise

**Middle ground (what chronymon's page does).**  h is a first-order
lag, `f += (1 - exp(-dt / tau)) (T - f)`.  For each tau on a grid
(none, 5 min to 4 h), filter T, fit c, k1 (and k2) by robust (Huber)
least squares, so steps and NTP outliers pull little, and score the
fit by blocked cross-validation: split the record into six contiguous
stretches, fit on the others (leaving out an hour either side), and
predict the held-out stretch from the temperature alone.  The tau with
the smallest held-out RMSE wins, and is reported against tau = 0.

Cross-validation, not the in-sample fit, because the frequency's
noise is slow and strongly correlated (an AR(1) fit gives rho about
0.99): consecutive samples are not independent, so in-sample
criteria reward any extra freedom.  A lag judged in-sample won where
there is none: on apu-0 a sensor step the frequency followed at once
was smeared into a trail, and 20 min "fitted" better.  Correcting for
the correlated noise instead (generalized least squares) fails the
other way: with rho near 1 it effectively differences the data and
loses the slope.  Held-out stretches sidestep both.

**Full system identification (the study's check).**  h is an
output-error model, y = c + B(q)/F(q) T, with B and F of order 1 or 2
and a dead time of 0 to 60 min, fitted by robust nonlinear least
squares on the simulated output, orders and dead time chosen by the
same blocked cross-validation.  Its step response shows the shape a
single lag cannot: on apu-0 and draws most of a step arrives at once,
the rest within 30 min; on tare almost nothing for 15 min, then most
within two hours.  It beats the middle ground clearly only on tare.

## Why

chronyd applies `tempcomp` to each raw sensor reading as it arrives
(`read_timeout`, `tempcomp.c`, chrony 4.6), with no smoothing.  Hosts
measured with chronymon's Temperature page and
[`study`](../study), on the hosts' data in [`data`](../data), show that the frequency follows the
sensor through a lag, and that sensors are noisy:

| Host | Sensor | Step | Reference | Lag | Held-out RMSE, no lag / lag, ppm |
|---|---|---|---|---|---|
| tare | CPU coretemp | 1 °C | NTP | 30–90 min | 0.77 / 0.26 |
| chronos | thermal zone | 0.454 °C | PPS | 30 min | 0.11 / 0.023 |
| draws | board sensor | 0.538 °C | PPS | 10 min | 0.029 / 0.020 |
| apu-0 | SoC, at the crystal | 0.1 °C | PPS | about 0 | 0.034 / 0.032 |

A line fitted with the lag but given raw readings makes things worse:
on chronos about 0.09 ppm RMS of fast error (about 3 us of offset per
32 s reading, twice its present RMS offset); on tare 0.67 ppm RMS,
peaks of 3 ppm.  The line needs the filtered temperature.

## Measurements

The lags above were fitted to chronyd's frequency, which is chronyd's
estimate, not the crystal's: each source's frequency is a regression
over up to 64 samples, and the tracking frequency blends each new
estimate with the old one by their skews, an average with a long
memory.  So a fitted lag mixes three things: the crystal's thermal
lag, chronyd's estimator, and smoothing of a noisy, discretized
sensor.  On chronos (held-out RMSE, ppm; none 0.066):

| Time constant | Causal lag | Zero-phase smoothing | Pure delay |
|---|---|---|---|
| 5 min | 0.055 | 0.056 | 0.061 |
| 10 min | 0.050 | 0.054 | 0.064 |
| 20 min | 0.046 | 0.054 | 0.070 |
| 30 min | 0.047 | 0.055 | 0.076 |
| 60 min | 0.057 | 0.059 | 0.096 |

Smoothing with no delay gets half the gain; the rest is a gradual
delay best at 20 to 30 min, no dead time.  Physically, a bare board
in still air (about 25 g of PCB, about 0.2 W/K of convection from its
faces) has a time constant of a few minutes, and an SMD crystal
follows its board in seconds: most of the fitted lag is likely
chronyd's estimator.

So chrony-tempcomp takes its own measurements, independent of
chronyd:

- **Temperatures.**  Every sensor on the host, about once a second,
  at full resolution, timestamped: the inputs for several sensors,
  enough samples to average across a sensor's steps, and no
  dependence on chronyd's `tempcomp` interval.
- **The crystal's frequency, from PPS against `CLOCK_MONOTONIC_RAW`.**
  chronyd does not use the kernel's PLL: it sets the kernel's
  frequency and tick (`adjtimex`) and slews by frequency, steering
  `CLOCK_REALTIME`.  `CLOCK_MONOTONIC_RAW` is the clocksource at a
  fixed scale, which nothing adjusts: it runs at the crystal's own
  rate.  The raw time between consecutive PPS edges is 1 s times one
  plus the crystal's frequency error, with no estimator and no
  compensation in it.  About 1 us of edge jitter averages to about
  0.02 ppm over a minute.
  - `/dev/ppsN`: the edge is stamped in `CLOCK_REALTIME`; map it to
    the raw clock by reading both clocks together just after each
    edge.
  - A PHC pin (`refclock PHC /dev/ptpN:extpps`): the edge is in the
    NIC's clock, another oscillator; map it to the raw clock the same
    way, reading the PHC (`clock_gettime` on the device) and the raw
    clock together just after each edge.  The NIC's oscillator only
    spans that short gap, so it hardly enters.  This needs no ioctl,
    so chronyd's timestamping is left alone; a cross-timestamp
    (`PTP_SYS_OFFSET_PRECISE`) would tighten the pairing if the reading
    proved too loose.
  - The raw clock's scale is the kernel's boot-time calibration of
    the clocksource, so its absolute error can differ from boot to
    boot: fit an intercept per boot.  A clocksource switch
    (`current_clocksource`, e.g. TSC to HPET) is treated as a reboot.
  - Without a PPS (an NTP-only host) there is no such measurement;
    fall back to chronyd's estimate, lag included.
- **chronyd's state**, read-only: synchronized or not and to what,
  steps and restarts, the compensation applied; for discarding bad
  stretches and freezing learning.
- **Its own record** of all of these, for fitting.

The lag to filter `tempcomp`'s temperature by is then the physical
one, measured this way; the proposal for chrony's guidance says so.

The quickest check, before any program: `study/pps_raw.py` reads a
PPS device's `assert` and `CLOCK_MONOTONIC_RAW` together on draws,
and `study/pps_step.py` gets the frequency from them, with a CPU load
step (`stress-ng --cpu 0`) to see how fast the crystal responds.  The
load should last about 30 min and the cooling longer, with a settled
baseline first: a tau near 30 min would not finish within the load.
A jump in frequency the moment the load changes is not thermal (supply
or load pulling the oscillator) and is fitted apart from the lag.

### First result: draws, 2026-11-05

One load step, 30 min of `stress-ng --cpu 0` on an idle host, with 30
min before and 60 min after ([data](../data/README.md)).  The sensor
went from 53 to 80 °C with a time constant of about 2.3 min.  The raw
frequency fell 1.3 ppm within 2 min, then climbed 0.7 ppm under load;
when the load ended it fell again, 0.7 ppm within 1 min, then climbed.

- **The crystal lags the sensor by about 1 min, not 10.**  A dip both
  ways is what one curve with a turn in it does.  Fitted on one side of
  the load's end and predicting the other, the frequency's parabola on
  the sensor through a lag has its least error at 0.75 to 1 min: 0.11
  to 0.13 ppm, the noise of a 15 s bin (0.133 ppm).  A straight line
  does no better than 0.40 ppm at any lag.
- **chronyd's estimator keeps up with a large step.**  chronyd's own
  frequency matches the raw one to +0.004 ppm (median), spread 0.052
  ppm, with its best lag behind the raw at 0 of the 1 min grid.  That
  says nothing of quiet running: the regression drops its oldest samples
  until the residuals pass a runs test (`RGR_FindBestRegression`,
  `regress.c`, chrony 4.6), so a step shortens its window, and in quiet
  running it can keep up to 64 samples (`MAX_SAMPLES`, `sourcestats.c`).
- **The curve turns at 68.6 °C on the sensor**, as
  `0.0057 (T - 68.6)^2 + 3.30` ppm through a 1 min lag.  draws's
  `tempcomp` line, fitted only below 61 °C, already has its turn at
  68.5 °C and a depth of 1.19 ppm.  The slope at 55 °C is -0.16 ppm/°C,
  against the -0.17 of 9 days' readings.

This is one step on one host, driven through the CPU, so it shows the
fast path from the SoC to the crystal; a slow path from the room could
not show in two hours.

### Why the readings fit a longer lag

draws's 9 days of readings, at their one-minute buckets
([`study/smoothing.py`](../study/smoothing.py),
[output](../data/smoothing.txt)), best fit a causal lag of 7 min, which
`lag.py`'s coarser grid gives as 10.  Held-out RMSE, ppm:

| | None | Causal lag | Same smoothing, no delay |
|---|---|---|---|
| The readings | 0.0395 | 0.0293 at 7 min | 0.0338 at 3 min |
| Control: crystal 1 min behind | 0.0477 | 0.0394 at 3 min | 0.0376 at 5 min |
| Control: crystal 5 min behind | 0.0464 | 0.0323 at 7 min | 0.0343 at 7 min |
| Control: crystal 7 min behind | 0.0484 | 0.0314 at 7 min | 0.0363 at 7 min |

Each control is a simulated crystal behind the host's slow temperature,
read through the sensor's 0.538 °C step and noise.

- **Smoothing is about half of the gain, not all.**  It takes 0.0395 to
  0.0338 with no delay.  With a 1 min crystal, smoothing alone should
  beat the causal lag; in the readings the causal lag wins, by what a
  crystal 5 to 7 min behind gives.
- **A second, slower path for load's heat is ruled out.**  In the
  readings, 60% through 1 min and 40% through 20 min scores 0.0276, the
  best of all; across the load step it predicts to 0.30 and 0.47 ppm
  against 0.11 and 0.15 with no second path.
- **What was left then: chronyd's window, or the room's path** (see
  "Load cycles").  In
  quiet running, 64 samples at 16 s is up to 17 min, whose regression
  centers about 8.5 min back, the size of the delay left over.  Or heat
  from the room reaches the crystal and the sensor by paths the load
  step does not use.
- **apu-0 fits less, but polls faster, so it does not separate them.**
  Its sensor is at the crystal and its step is 0.1 °C, and its readings
  fit a crystal 1 to 3 min behind
  ([output](../data/smoothing-apu-0.txt)): 0.0338 ppm causal and 0.0346
  with no delay, both at 3 min, against 0.0382 with none.  But its PPS
  polls every 4 s (`poll 2`) and draws's every 16 s, so 64 samples span
  at most 4.3 min there, centered about 2 min back, and 17 min on
  draws, centered about 8.5: the same ratio as their fitted delays,
  which is what chronyd's window would give.  A short sensor path on
  apu-0 would give it too.  One `sourcestats` on apu-0 on 2026-11-05
  showed 6 samples over 20 s, and one on draws 9 over 129 s, both well
  under that limit.  Over 50 h draws kept a median 86 s ("The 10 min").
- `frequency - compensation` also holds the raw reading, through the
  compensation, which favors no lag: a 1 min delay scores worse than
  none, and worse than 2 min.  These readings cannot fully separate it.

### Load cycles

On 2026-11-06 apu-0 and draws each recorded raw frequency for 12 h: one
30 min load step, about 4.5 h quiet, then 4 cycles of 15 min of
`stress-ng --cpu 0` and 15 min idle, then quiet
([`study/pps_cycles.py`](../study/pps_cycles.py),
[output](../data/cycles.txt)).  Every cycle crosses the same
temperatures, so predicting one from the others interpolates.  apu-0's
two spans without a GPS fix are left out.

- **draws's crystal lags its sensor by 0.75 min.**  Each cycle predicted
  from the others is best at 0.75 min, all four alike, 0.133 ppm against
  0.174 with no lag and 0.393 at 5 min; 15 s bins have about 0.13 ppm of
  noise.  Fitted under load and judged out of it, and the reverse, agree
  (0.5 to 0.75 min).  The room changes nothing: 0.130 with it, same lag.
- **apu-0's is 2 to 5 min, depending on the room.**  Its load moves the
  sensor only 2 °C, while the room moved 2 °C over the same 2 h.  Sensor
  alone is best at 5 min (0.0215 ppm; folds 5 to 7), or 4 by load and
  rest.  With the room, best at 1.75 min (0.0135; folds 1.25 to 2.5), or
  2 by load and rest.
- **A model with the room, frozen beforehand, did not hold up.**  Each
  host's best sensor-and-room lags were chosen from 45 on the first
  load step and the quiet after it, and scored 0.005 ppm (apu-0) and
  0.083 (draws) there, against 0.078 and 0.46 for the sensor at 1 min.
  That was selection on the scored data.  Frozen, with the coefficients
  fitted only on the quiet stretch before the cycles (RMSE, ppm):

  | | Cycles | After |
  |---|---|---|
  | apu-0, sensor 1 min | 0.045 | 0.036 |
  | apu-0, sensor 3 + room 10 min | 0.026 | 0.066 |
  | draws, sensor 1 min | 0.85 | 0.042 |
  | draws, sensor 1 + room 30 min | 0.55 | 0.026 |

  It ranks 17th and 14th of the 45 on apu-0, 20th and 3rd on draws, so
  the choice predicted little.  All of the cycles and half of the after
  are outside the sensor range the quiet fit saw, which is why draws's
  cycles are poor for every model.  Fitted on all before the cycles,
  load step included, the room makes draws's cycles worse (0.46 against
  0.26).  Over 9 to 10 days, sensor and room are 0.95 to 0.98 correlated,
  so their coefficients trade off.

So the fast path is measured, on draws to a quarter of a minute, and
draws's 7 min is not it.  The room may be part of the rest, but this does
not show it.  Nor is chronyd's window all of it: over the 4 h quiet stretch
the raw frequency, like chronyd's, fits 10 to 11 min by chronymon's method,
though a trend that long pins no lag.  Its window can still add a delay
of about 47 s (half the median 95 s), which these runs cannot rule out.

### Held-out days, and what the 7 min was

Each whole day predicted from the others, its lags chosen on the others
too ([`study/room_days.py`](../study/room_days.py),
[output](../data/room-days.txt)).  These are chronyd's readings, with its
window in them; the room is apu-0's stick.

draws's 7 min is a blend.  Its `tempcomp` line came on at 2026-11-01 00:34
UTC, and the days either side disagree (held-out RMSE, ppm):

| draws | Sensor, 0 min | 1 min | 7 min | 10 min | Lags chosen on other days | With the room |
|---|---|---|---|---|---|---|
| Compensation off, 5 days | 0.0419 | 0.0334 | 0.0192 | **0.0186** | 0.0186, 10 min | 0.0177 |
| Compensation on, 3 days | **0.0329** | 0.0380 | 0.0399 | 0.0404 | 0.0329, 0 min | 0.0296 |

- **With compensation on, the readings cannot give a lag.**  Frequency
  less compensation holds the raw reading, which favors none (above), and
  minute-to-minute noise goes from about 0.008 to 0.055 ppm.
- **With it off, draws fits about 10 min** in quiet running, against 0.75
  min under load.  The raw frequency's 10 to 11 min over 4 h agrees.
- **The room does not close that gap on draws.**  The sensor at its fast
  lag with the room scores 0.0250, against 0.0186 for the sensor alone at
  10 min; with the room's own best lags the gain is 5%.
- **On apu-0 the room helps**: 0.0317 to 0.0260, better on 8 of 9 days.
  But its compensation is on throughout, and the lags chosen (sensor 15
  min, room 5 to 10) are not the 1.75 min of its cycles, so they are a
  fit, not a path.

What was left, for draws: a real slow path that the room's stick does not
capture, the 0.538 degree sensor step (a lag also smooths it), or chronyd's
window.

### The 10 min

`smoothing.py --compensation off` on draws's 5 clean days
([output](../data/smoothing-off.txt)), held-out RMSE, ppm:

| | None | Causal lag | Same smoothing, no delay | Pure delay |
|---|---|---|---|---|
| The readings | 0.0420 | **0.0179 at 10 min** | 0.0278 at 7 min | 0.0383 at 2 min |
| Control: crystal 1 min behind | 0.0449 | 0.0373 at 3 min | 0.0352 at 7 min | |
| Control: crystal 7 min behind | 0.0405 | 0.0219 at 7 min | 0.0264 at 10 min | |
| Control: crystal 10 min behind | 0.0446 | 0.0205 at 10 min | 0.0317 at 10 min | |

- **The sensor's step does not explain it.**  Through the 0.538 °C step a
  1 min crystal fits 3 min, and smoothing with no delay beats the causal
  lag.  The readings show the reverse, by what a crystal 7 to 10 min
  behind gives.
- **Nor does chronyd's window.**  Over draws's 50 h recording
  (2026-11-06 to 08, 602 `sourcestats` every 5 min) GPS kept 6 to 25
  samples, spanning a median of 86 s (90th percentile 175 s, longest
  385 s): a delay of about 45 s, not 10 min.  That recording had
  compensation on; the window is set by the PPS's noise, not by it.
- **Two paths fit as well as one slow lag:** 20% through 1 min and 80%
  through 10 min scores 0.0180, against 0.0179.

So, on chronyd's readings, draws's crystal follows the quiet room's slow
changes about 10 min behind its sensor, and the CPU's load 0.75 min
behind.  Heat from the load reaches the sensor and the crystal by
one path, and from the room by others; the room's stick sees too little of
it to help.  Raw frequency over days, free of chronyd and the
compensation, is to confirm it.

## Phase 1: a fixed filter

- Read the sensor (`/sys/.../temp*_input`) every `period` seconds.
- First-order filter, time constant `tau`:
  `f += (1 - exp(-dt / tau)) * (reading - f)`; restart from the
  reading after a gap of many `tau`.
- Write `f`, in the sensor's own units, atomically (temp file, then
  rename) to e.g. `/run/chrony-tempcomp/temp`.  chrony.conf's
  `tempcomp` points at it, with the line chronymon fitted with the
  lag on.
- Save the filter's state to `/run` and reload it on restart, so a
  restart mid-day does not reset it to the raw reading.
- Settings: sensor, `tau`, `period`, output path; flags, or
  `/etc/default/chrony-tempcomp`.
- systemd unit: `Restart=always`, ordered after `chrony.service` and
  part of it, `WatchdogSec` with `sd_notify` pings.

[`tools/tempcomp-filter.sh`](../tools/tempcomp-filter.sh) is this as a
shell script, with [its unit](../tools/tempcomp-filter.service), packaged
by [`tools/build_deb.sh`](../tools/build_deb.sh) as `tempcomp-filter`.
It writes `/run/chrony/tempcomp-filter`: Debian's AppArmor profile for
chronyd reads only sysfs sensors and chrony's own runtime directory, and
its only extension point, `local/`, is the admin's, not a package's.  So
it runs as `_chrony`, that directory's owner (0700), with no capabilities,
after chrony, which makes the directory, and part of it, as the directory
goes when chrony stops; a failed write is logged and retried.  chronyd
reads its file once at start, before the filter, so at boot it logs one
"Could not read temperature" and takes the value 15 s later.  It does not
start until the sensor and the off value are set (`ExecCondition`):
`/etc/default/tempcomp-filter`, shipped commented out, then
`/etc/tempcomp-filter.d/*.conf`.  A host's settings and its `tempcomp`
line are a package of their own, `tempcomp-filter-HOST`, from
[`tools/hosts`](../tools/hosts): the line in its own conf.d file,
`tempcomp-filter.conf`, in place of `tempcomp.conf`.  Every change to a
host comes in a package; config files are replaced, never edited.  The
unit is `Type=simple`: systemd 252 can lose a short-lived
`systemd-notify`'s `READY=1`, failing the start, where a lost watchdog
ping is covered by the next.  `ALTERNATE_TAU` sets another time constant
for odd (or, with `ALTERNATE_DAYS`, even) UTC days, to compare two on a
host, or `off`: `OFF_VALUE` written those days, no compensation, the
filter running on at `TAU` so the next day starts settled (status, test
2).

A Kalman filter with this model (the crystal's temperature as the
state, a first-order lag as its process, the sensor's jitter as the
measurement noise) settles to a fixed gain: the same exponential
filter.  It earns its place in phase 2, not here, beyond setting the
gain from the noise and handling irregular readings and gaps.

Phase 1 can start as a shell script and a unit on one host, to measure
the gain (offset RMS, and holdover with the reference cut) against
tempcomp off, before any of this is written properly.

## Fail-safes

chronyd keeps its last compensation when it cannot read the file: it
logs "Could not read temperature" and carries on (`read_timeout`).  So
removing the file does not turn compensation off.

- On removing the package, write T0 (`tempcomp-filter off`): zero
  compensation, the same as tempcomp off.  Not on every stop: a restart
  (an upgrade, a new `tau`) is a stop too, and chronyd reading zero for a
  few seconds is worse than a value a few seconds old.  So a stop, like
  a crash, leaves the last value, and a restart carries on from it.
- A hang (alive but not writing) is caught by the systemd watchdog
  and restarted.
- A crash leaves the last value, which is at worst a stale but sane
  temperature; `Restart=always` brings it back.
- chronyd ignores a compensation past 10 ppm, so a wrong line cannot
  push the clock past that.

## Phase 2: chronyd's own steps, then learning

Three steps, smallest first; each stands alone.

1. **A lag on `tempcomp`** ("Proposal for chrony", below).  Phase 1 is
   the evidence.
2. **chronyd accounting for its own `tempcomp` steps** (below).
   Open: which is right depends on two delays, not yet measured.
3. **A learned line** ("Learning", below), prototyped in this program
   first; upstream only after months of holdover tests, if ever.

Not planned: applying `tempcomp` only when chronyd is not synchronized.
With the filter, compensation cost little while locked (test 2), less
than a mode switch, a lag in noticing the loss and a hand-back on
re-lock would add.  Compensation on GPS loss
(feed-forward) needs nothing new: `tempcomp` already keeps adjusting on
top of chronyd's last frequency estimate in holdover, and step 3 makes
that adjustment right.

### chronyd's own steps

`LCL_SetTempComp` sets the kernel's frequency without calling the
parameter change handlers; `LCL_SetAbsoluteFrequency` and
`LCL_AccumulateDeltaFrequency`, the loop's own changes, call them
(`local.c`, chrony 4.6; `LCL_SetTempComp` and `tempcomp.c` unchanged
in substance on master at c97d808).  So each change in the compensation is a
frequency step the rest of chronyd is not told about: the samples
before and after it are fitted as one, and the estimate chases the
bend over the next updates.  That is the noise phase 1 measured with
raw readings, and why no correction was quietest while locked (test 2).

Calling the handlers is not the fix.  The handlers (`slew_sources`,
`sources.c`; `slew_samples`, `refclock.c`) call `SST_SlewSamples` and
`SPF_SlewSamples`, which rewrite each stored sample "as though [it]
were sampled under the new regime" and shift the estimated frequency
by the change (`sourcestats.c`).  That assumes the clock's own rate
did not change, true of the loop's own adjustments.

Which is right for a `tempcomp` change depends on two delays after the
crystal shifts by d:

- L, until the compensation has moved by about d: the thermal path to
  the sensor, the sensor's processing, and the `tempcomp` interval.
  Never zero.
- Lloop, until chronyd's frequency estimate has taken in d from the
  reference's offsets: several updates of its regression; on the
  order of a minute or more with a PPS at poll 4, far longer over a
  network.

If L is much shorter than Lloop, the compensation cancels the crystal
before the loop sees it; the error is d over L, and not calling the
handlers is right.  If L is much longer, the loop has already taken
the shift in, and the compensation applies it a second time; without
the handlers the loop finds that only from new offsets and unwinds it
over its regression window, while with them its estimate would shift
at once and the next update remove it.  In holdover there is no loop,
and any compensation earlier than the temperature changes helps.

chronyd does not know where it sits between the two, so neither fixed
choice suits both; a good reference shortens Lloop and moves it toward
the second.  This is a real question for upstream, not simply a bug.
Next: measure Lloop (chronyd's response to a known frequency step,
from its logs or under simulation) and L for a host, before deciding.

### Learning

Refit `tau` and the line periodically (daily) from the clock's own
frequency (chronyd's frequency less the compensation) against the
temperature, the method chronymon's page uses: robust (Huber) fits,
`tau` chosen by blocked cross-validation.  Adopt a candidate only if:

- it predicts held-out readings better than both the current fit and
  no compensation;
- the temperature spread over the window is at least a minimum;
- coefficients stay within bounds, and change by at most a limit per
  refit.

Freeze learning while chronyd is not synchronized (holdover is when
tempcomp matters most and the frequency is not measured), and drop
readings near restarts and steps.  The learned fit is kept in
`/var/lib/chrony-tempcomp` and each adoption logged, old and new.

A Kalman filter is the online alternative to the daily refit.  With
`tau` fixed (phase 1's filter), it is recursive least squares: the
line's coefficients are the state, each a random walk, so k0 also
absorbs aging; the measurement is the clock's own frequency against
the filtered temperature, weighted by chronyd's skew.  It is linear,
and gives the coefficients' uncertainty.  Holdover handles itself:
with no frequency measured, the update is skipped, learning stops and
prediction carries on.  Learning `tau` too (the crystal's temperature
as a state) makes it nonlinear (extended or unscented); not first.
Costs: the random walks' variances must be chosen or estimated
(innovation statistics); it learns from bad data after a step or from
a bad reference; with little temperature spread it confuses aging
with temperature; and a mistuned filter fails quietly, so the guards
above still apply.  The daily refit, whose results chronymon's page
can reproduce, is the baseline it must beat in the replay.

Since the program would write the compensation itself, it could write
the predicted correction directly (tempcomp `0 0 1 0`) and use models
chrony's formula cannot, e.g. a two-speed response.  Not in the first
versions: chrony.conf would no longer say what is applied.

Source of the frequency: chronyd's tracking report (read-only control
protocol) or chronymon's database.  Undecided; the first keeps the two
programs independent.

## Several sensors

Most hosts have more than one temperature sensor: a CPU's package and
cores, an NVMe drive, a Wi-Fi radio, a board.  The crystal's
temperature depends on several heat sources, and each sensor sees a
different mix of them through its own path: on tare, the CPU follows
load within a minute, while the drive and the radio follow the case
and the room more slowly.  A single sensor mixes these together; one
lag cannot follow both.

Multiple-input, single-output (MISO) system identification fits the
crystal's temperature as a combination of the sensors, each through
its own lag:

    Tc = c + sum over sensors i of  w_i * lag(T_i, tau_i)

and the frequency against Tc as before, the weights and lags chosen
by the same robust fits and blocked cross-validation, and judged
against the best single sensor.

- **One output.**  chronyd's `tempcomp` reads one file, so the
  program writes the combined estimate there (or the correction
  itself, tempcomp `0 0 1 0`).  This needs the separate program; the
  proposal for chrony stays the single-sensor case.
- **The sensors move together.**  All of them follow load and the
  room, so a fit can trade one off against another with large,
  opposing weights that fit well and predict badly.  It needs few
  inputs, regularization (ridge), and held-out stretches to judge it
  by.
- **More data.**  More parameters need more days, over more
  conditions, than a single lag does.
- **Each sensor's own step and noise** (see "Discretization"): the
  coarse ones need longer lags or smaller weights.
- **Recording first.**  chronyd logs only its `tempcomp` sensor, so
  there is no history of the others.  The first step is a logger
  recording every sensor on a host or two for a week or more, then
  the model offline (numpy), before anything is built.

## CLI

Over a Unix socket in `/run`; `status` unprivileged, the rest root or
a group.

- `status`: current `tau` and line, last refit, held-out errors
  (current, last candidate, none), frozen or not and why.
- `refit [--days N] [--apply] [--force]`: show a candidate; adopt it
  with `--apply`, guards overridden with `--force`.
- `freeze` / `resume`: stop or restart learning.
- `reset`: back to the configured `tau` and line.
- `off`: write T0 and hold it.

Not a button on chronymon's page: that page is served without
authentication and stays read-only.

## Effects on chronymon

chronyd's `tempcomp.log` records what chronyd read, so it would hold
the filtered temperature; the page then fits against it, and its lag
should come out near zero, a check that `tau` is right.  The raw
sensor drops out of the record unless chronymond reads it directly, a
separate, observe-only feature.

## Proposal for chrony

The cleaner home for the filter is chronyd itself: one number, a lag,
on `tempcomp`.  It removes the separate program, and with it the
stale-file fault, for anyone who only needs the filter.

### Syntax

An optional keyword at the end of either form, in chrony's usual
style for options:

    tempcomp <file> <interval> <T0> <k0> <k1> <k2> lag <seconds>
    tempcomp <file> <interval> <points-file> lag <seconds>

No `lag`, or `lag 0`, is today's behavior.

### Behavior

In `read_timeout`, before the reading reaches the formula or the
points:

    f += (1 - exp(-dt / lag)) * (T - f)

with `dt` the time since the last good reading.

- The filter starts from the first good reading.
- A failed read skips the update and keeps the state; a gap of many
  lags starts it again from the next good reading.
- The filtered value goes through the existing formula or points and
  the existing 10 ppm limit, unchanged.

### Log

`tempcomp.log` gains the filtered temperature as a new last column,
after the raw reading and the compensation, so a tool can fit either
and see what was applied.  The existing columns keep their places.

### Documentation

A paragraph in `chrony.conf.adoc`: when to use it (a sensor away from
the crystal, so the crystal warms more slowly than the sensor; or a
noisy or coarsely discretized sensor), and how to choose the value
(fit the frequency against the temperature through a first-order lag
for a range of lags, and keep the one that best predicts readings
left out of the fit).  It does not promise that the value is the
crystal's thermal time constant: a lag fitted from chronyd's
frequency also includes chronyd's own smoothing of its estimate,
larger with NTP than with a PPS.

### Why one number

A second number was tried on the same data: an immediate share (part
of the temperature reaching the crystal at once, the rest through the
lag), and a dead time.  Held-out RMSE, ppm, lag alone / with the
share / with the dead time: apu-0 0.032 / 0.029 / 0.032, draws
0.020 / 0.019 / 0.020, chronos 0.046 / 0.041 / 0.046, tare 0.37 /
0.37 / 0.31.  The share gains at most 0.005 ppm (under 0.1 ns over a
16 s PPS update), within the uncertainty of a few days' data, and
passes its share of the sensor's noise and steps straight through;
the dead time helps only tare, an NTP host.  The lag alone gets nearly
all of the gain.

### Case to make

- Evidence: the held-out RMSE per host, no lag against lag ("Why",
  above), and, from phase 1, offset and holdover before and after.
  Lean on the PPS hosts, whose frequency chronyd estimates with
  little smoothing of its own.
- The likely answer, "write the file with an external program": a
  helper's file going stale keeps chronyd compensating with its last
  value, silently (`read_timeout` keeps the last compensation when it
  cannot read); a helper is one more package and service; and the
  smoothing is part of the compensation's model.
- The discretization: on chronos every flip of the reading between
  two steps jumps the compensation by about 0.09 ppm, which the lag
  averages away.

### Process

Ask on chrony-dev first, with the problem, the evidence and the
syntax, before writing the patch; the maintainer's answer shapes it.
Then a patch against chrony's current release, tested on a host built
from source.  chrony takes patches on the mailing list, not as merge
requests.

## Open

- Host for the phase 1 experiment.
- Frequency source for learning: chronyd or chronymon's database.
- Learning: the daily refit as the baseline, recursive least squares
  as the candidate; replay before either runs on a host.
- Proposal for chrony: when to ask.
- A simulation of chronyd's loop for testing: the real chronyd under a
  simulated clock, or a model of it.
- Several sensors: which hosts to record, and for how long.
- Measurements: why draws fits about 10 min in quiet running and 0.75 min
  under load, from days of raw frequency; the same check on another host
  (chronos, once it is ready).
