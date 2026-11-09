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
  chronyd nothing.  With `--phc /dev/ptpN` it reads a PHC pin's edges
  instead, read-only and with no ioctl, needing the device's group.
- `pps_step.py` turns the rows into the frequency per minute, positive
  fast (chronyd's sign: `client.c`, `chrony.conf.adoc`, chrony 4.6), and
  finds the load steps from the CPU's load.  For each step it fits how
  the frequency and each sensor moved.  Then it fits the frequency's
  curve on each sensor through each lag on one side of the load's end
  and predicts the other: both cross the same temperatures, where blocked
  cross-validation of a step run would extrapolate.  Given chronyd's
  readings, it scans chronyd's lag behind the raw frequency too.

A step's tau range assumes independent noise, so it is optimistic; a
step's start also carries any settling left from the step before.  A
frequency that crosses a turn in its curve is not first-order, so its
step fit means little: judge it by the cross-prediction.

A PPS is only the crystal's reference while its receiver has a fix: a
u-blox without one read 0.83 ppm off and came back with a 1.8 ms step.
`pps_step.py` does not know; check the receiver's log for such spans.

`pps_cycles.py`, given repeated load cycles, predicts each cycle from
the others for the fast lag, with and without the room's temperature,
and scores models frozen before the cycles.  `bin` reduces a recording,
leaving out `--unusable` spans; `report` runs on what it saved.

`room_days.py` predicts each whole day from the others, its lags chosen
on the others too, with and without the room's temperature, on all days
and on those with compensation off and on apart: frequency less
compensation holds the raw reading, so those with it on cannot give a lag.

`raw_minutes.py` puts recordings, one or several files of a host, in
`fetch.py`'s one-minute form with the raw frequency and no compensation,
leaving out `--exclude` spans (no fix; a load step and its settling), so
`room_days.py` and `smoothing.py` judge the crystal free of chronyd:

    python3 raw_minutes.py pps-*.csv --sensor /sys/class/hwmon/hwmon0/temp1_input \
        --exclude FROM:TO ... -o draws-raw.npz

`filter_gain.py` replays recordings through the `tempcomp` line, the
sensor read as chronyd reads it and through the filter at each time
constant, and scores each against the crystal's own frequency: RMS, day
by day, and the time error over 1, 4 and 24 h of holdover.  And the noise
each pushes into chronyd: the RMS change of the compensation from one of
chronyd's readings to the next, a frequency step its loop chases.  With
`--fit`, each time constant gets its own line, fitted to the crystal
through that filter, and each day is scored on the line fitted to the
other days.

`smoothing.py` asks what a fitted lag is, on a host's one-minute
buckets: a causal lag against the same smoothing with no delay and a
pure delay; simulated crystals of known lag as a control; and a second,
slower path, judged on the readings and on a load step's run
(`--run`).  `--compensation off` or `on` keeps only those whole days, as
`room_days.py` splits them.

The design's result is `pps_step.py --bin 15` on
[`data/draws-pps.csv`](../data/draws-pps.csv), and for the comparison
`--bin 60 --chronyd ../data/draws-pps-chronyd.npz`.

Tests: `uv run pytest`.
