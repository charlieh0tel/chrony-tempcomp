# Data

Each host's readings, fetched from its chronymon with
[`study/fetch.py`](../study/fetch.py) on 2026-11-04, in one-minute
buckets, since the host's `tempcomp` sensor last changed, at most 14
days.  NumPy `.npz`, each array one value per bucket, NaN where the
bucket has none:

- `at`: the bucket's time, Unix seconds.
- `temperature`: chronyd's `tempcomp` sensor reading, millidegrees
  Celsius, as `tempcomp.log` has it.
- `compensation`: the compensation chronyd applied, ppm, from
  `tempcomp.log`.
- `frequency`: chronyd's frequency, ppm, from its tracking report or
  `tracking.log`.  The clock's own frequency is `frequency -
  compensation`.

| File | Host | Sensor | Step | Reference | From | To (UTC) |
|---|---|---|---|---|---|---|
| `apu-0.npz` | apu-0 | SoC thermal zone | 0.1 °C | PPS | 2026-10-25 01:43 | 2026-11-04 08:11 |
| `chronos.npz` | chronos | thermal zone | 0.454 °C | PPS | 2026-11-02 18:18 | 2026-11-04 08:10 |
| `draws.npz` | draws | board sensor | 0.538 °C | PPS | 2026-10-26 02:24 | 2026-11-04 08:11 |
| `tare.npz` | tare | CPU coretemp | 1 °C | NTP | 2026-10-31 22:03 | 2026-11-04 08:10 |

The steps were measured on 2026-11-04 from an hour of each sensor's
raw readings, the gaps between distinct values.  apu-0's compensation
from 2026-10-25 17:10 on is a point table with the drift's own sign,
which doubles the drift in `frequency`; `frequency - compensation` is
unaffected.

`lag.txt` and `sysid.txt` are `study/lag.py` and `study/sysid.py` on
these files.
