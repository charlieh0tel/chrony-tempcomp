# chrony-tempcomp

Fit a [chrony](https://chrony-project.org/) `tempcomp` point table from
chrony's own logs, for a clock disciplined by a good reference (PPS) whose
oscillator drifts with temperature.

A point table rather than the `k0 k1 k2` quadratic, because real crystals
are not always quadratic: one test box's has a +3 ppm anomaly between 80.5 and
82 C on the board sensor that no polynomial follows.

## Needs

- python3 with numpy
- chrony logging both, in `chrony.conf` or `conf.d`:

      log tracking tempcomp
      tempcomp /sys/class/thermal/thermal_zone0/temp 15 0 0 0 0

  A `tempcomp` line with zero coefficients logs the sensor without
  compensating.  An existing point table is fine too: tracking.log records
  the uncompensated frequency either way, so refits need no change.
- Some days of logs covering the temperatures the box sees.

## Use

`/var/log/chrony` is readable only by `_chrony`, so copy it first:

    sudo sh -c 'mkdir -p /tmp/chronylogs && cp -a /var/log/chrony/. /tmp/chronylogs/ && chown -R $SUDO_USER: /tmp/chronylogs'

Fit, checking against the last day held out:

    ./chrony_tempcomp.py /tmp/chronylogs --holdout-days 1 \
        --extend-low 50000 --extend-high 90000 -o tempcomp.points

Install it, with a backup of `/etc/chrony` and rollback if chronyd fails:

    sudo ./install-points.sh tempcomp.points

The report goes to stderr.  Bins flagged "frequency moves fast here" are
where a little sensor noise is worth a lot of ppm.

## Notes

- Temperatures are in the sensor's units (millidegrees for sysfs).
- Each point is `comp = freq(T) - fref`.  chrony applies
  `freq = uncomp - comp`, so the compensated frequency sits at `fref`
  (default: the median).  chrony ignores any |comp| above 10 ppm.
- chrony interpolates linearly and extends the first and last segments, so
  pad the ends: `--extend-low` adds a quadratic guess through the coolest
  data and `--extend-high` holds the hottest value flat.  Both are guesses;
  refit once the logs cover those temperatures.
- chronyd exits on any comment or blank line in the points file, so
  the script never writes one and `install-points.sh` checks for them.
- `--source` picks which reference's tracking rows to trust (default
  `PPS`).  Rows while chrony followed a network source are much noisier.
- A board or CPU sensor beats an ambient one: on one test box the acpitz sensor
  predicted frequency to 0.018 ppm below the anomaly against 0.066 ppm for
  a TEMPer stick beside the box, with no lag to correct.

## License

GPL-3.0-or-later; see LICENSE.
