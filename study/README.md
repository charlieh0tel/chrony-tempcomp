# tempcomp study

How a host's clock frequency follows its temperature sensor, from
[chronymon](https://github.com/charlieh0tel/chronymon)'s API: the study
behind the lag in [the design](../docs/design.md) and chronymon's
Temperature page.  Needs numpy and scipy.

    python3 fetch.py --days 14 apu-0 chronos   # apu-0.npz, chronos.npz
    python3 lag.py apu-0.npz chronos.npz
    python3 sysid.py apu-0.npz chronos.npz

The data the design's figures come from, and these scripts' output on
it, are in [`data`](../data).

- `fetch.py` fetches one-minute buckets of the temperature, the
  compensation and the frequency, since the sensor last changed.
- `lag.py` is chronymon's Temperature page's method, as a reference: robust (Huber) fits
  of the clock's own frequency against the temperature through a
  first-order lag, the lag chosen by blocked cross-validation (six
  contiguous folds, each held out in turn and predicted from the
  temperature alone) and compared with none.
- `sysid.py` fits output-error models, their orders and dead time
  chosen the same way, and reports each one's step response: how
  much of a step of the temperature reaches the frequency in 5, 30
  and 120 minutes.  A sensor beside the crystal shows most of it at
  once.

Readings are averaged to five minutes and the frequency interpolated
across gaps of up to half an hour, as an NTP-only chronyd updates it
every few minutes.

- `apumon.sh` prints the acpitz and k10temp sensors at 1 Hz;
  `filter_temps.py` drops corrupt rows from such a log, `temps.csv`.

## The crystal's frequency, without chronyd

`fetch.py` and the fits above see the crystal through chronyd's
estimate.  These measure it directly (the design's "Measurements"):
PPS edges against `CLOCK_MONOTONIC_RAW`, with every sensor and the
CPU's load.

    python3 pps_raw.py -o draws-pps.csv                # on the host
    python3 fetch.py --days 1 draws                    # optional, chronyd's view
    python3 pps_step.py draws-pps.csv --chronyd draws.npz

- `pps_raw.py`, standard library only, records a row per edge.  It
  reads sysfs, `/proc` and the clocks, needs no privileges and sends
  chronyd nothing.
- `pps_step.py` turns the rows into the frequency per minute, positive
  fast (chronyd's sign: `client.c`, `chrony.conf.adoc`, chrony 4.6), and
  finds the load steps from the CPU's load.  For each step it fits how
  the frequency and each sensor moved, and for the whole run it scans
  each sensor's lag behind which the frequency best follows, by the
  same blocked cross-validation.  Given chronyd's readings, it scans
  chronyd's lag behind the raw frequency too: that part of a fitted lag
  is chronyd's estimator, not the crystal.

A step's tau range assumes independent noise, so it is optimistic; a
step's start also carries any settling left from the step before.

Tests: `uv run pytest`.
