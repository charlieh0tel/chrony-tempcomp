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

`draws-pps.csv` is `study/pps_raw.py` on draws, 2026-11-05 17:14:50 to
19:16 UTC: 30 min idle, `stress-ng --cpu 0` for 30 min from 17:45:16,
then cooling.  Its first lines say what it is from; `stamp_*` were
recorded as `real_*`, and `unix_read_ns` is `stamp_read_ns`, as
`CLOCK_REALTIME` stamped these edges.  `draws-pps.txt` is
`study/pps_step.py --bin 15` on it.  `draws-pps-chronyd.npz` is
`study/fetch.py --days 1 draws` just after it, for `--chronyd`.

`apu-0-pps-check.csv` is `study/pps_raw.py --phc /dev/ptp2` on apu-0,
2026-11-06 07:27:57 to 07:29:56 UTC, the recorder's first real run on a
PHC pin, idle: a row per event, of both pulse edges.

`apu-0-cycles.npz` and `draws-cycles.npz` are `study/pps_cycles.py bin`
on a recording of 2026-11-06 15:50 to 2026-11-07 04:14 UTC, with the
room's temperature from apu-0's `smartclock-sensord` database (a TEMPer
stick, every 10 s, in the same corner of the room).  `at_N`, `ppm_N`,
`sensor_N` and `ambient_N` are N s bins, 15 and 60; `load_on` and
`load_off` the 4 cycles' times; `first_off` the end of the load step
before them; `unusable` apu-0's spans without a GPS fix.  The raw CSV
files, 11 and 6 MB, are not here.  `cycles.txt` is its `report` on them:

    pps_cycles.py report apu-0-cycles.npz --sensor-lag 3 --ambient-lag 10 --sensor-only-lag 15
    pps_cycles.py report draws-cycles.npz --sensor-lag 1 --ambient-lag 30 --sensor-only-lag 2

`smoothing.txt` is `study/smoothing.py draws.npz --run draws-pps.csv`,
and `smoothing-apu-0.txt` is `study/smoothing.py --step 0.1 apu-0.npz`.
