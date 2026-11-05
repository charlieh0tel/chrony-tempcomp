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
