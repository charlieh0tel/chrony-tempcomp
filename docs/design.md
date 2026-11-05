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
`LCL_SetTempComp`, `tempcomp.c`, chrony 4.6).  There is no smoothing:
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
    NIC's clock, another oscillator; bring it onto the raw clock with
    the PHC's cross-timestamp (`PTP_SYS_OFFSET_PRECISE`, which gives
    raw time, where the driver supports it).
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
- **What is left, untested: chronyd's window, or the room's path.**  In
  quiet running, 64 samples at 16 s is up to 17 min, whose regression
  centers about 8.5 min back, the size of the delay left over.  Or heat
  from the room reaches the crystal and the sensor by paths the load
  step does not use.
- **apu-0 weighs against chronyd's window.**  Its sensor is at the
  crystal and its step is 0.1 °C, so little but chronyd's window should
  be left to fit, and its readings fit far less
  ([output](../data/smoothing-apu-0.txt)): 0.0338 ppm causal and 0.0346
  with no delay, both at 3 min, against 0.0382 with none, as a crystal
  1 to 3 min behind gives.  If chronyd keeps as many samples there as
  on draws (not yet checked), its window is at most that, and most of
  draws's delay is a path.
- `frequency - compensation` also holds the raw reading, through the
  compensation, which favors no lag: a 1 min delay scores worse than
  none, and worse than 2 min.  These readings cannot fully separate it.

Telling these apart needs the raw frequency, free of chronyd and of
the compensation, over days of quiet running: `pps_raw.py` for days,
with the number of samples chronyd is using
(`chronyc sourcestats`, read-only).

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
- systemd unit: `Restart=always`, ordered before `chrony.service`,
  `WatchdogSec` with `sd_notify` pings.

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

- On stop (`ExecStopPost`), write T0: zero compensation, the same as
  tempcomp off.
- A hang (alive but not writing) is caught by the systemd watchdog
  and restarted.
- A crash leaves the last value, which is at worst a stale but sane
  temperature; `Restart=always` brings it back.
- chronyd ignores a compensation past 10 ppm, so a wrong line cannot
  push the clock past that.

## Phase 2: guarded learning

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

A Kalman filter is the online alternative to the daily refit: state
the crystal's temperature and the line's coefficients, measurements
the sensor reading and the clock's own frequency, so it estimates the
crystal's temperature and learns the line together, with their
uncertainty.  Holdover handles itself: with no frequency measured,
the update is skipped, learning stops and prediction carries on.
Costs: the noise variances must be chosen, tuned or estimated (EM,
innovation statistics); the coefficients as states make it nonlinear
(extended or unscented); and a mistuned filter fails quietly, so the
guards above still apply.  Start with the daily refit, whose results
chronymon's page can reproduce; consider the Kalman filter once that
is understood.

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
- Learning by daily refit or by Kalman filter.
- Proposal for chrony: ask before or after the phase 1 experiment.
- Several sensors: which hosts to record, and for how long.
- Measurements: whether draws's leftover delay is chronyd's window or
  the room's path, from days of raw frequency; the same check on
  another host (chronos, once it is ready).
