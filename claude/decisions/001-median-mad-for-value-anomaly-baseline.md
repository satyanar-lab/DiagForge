# ADR 001 — Median + MAD baseline for value-anomaly detection

**Status:** accepted (T0L.4)
**Date:** 2026-05-24

`detect_value_anomalies` uses the sample median and median absolute deviation
(scaled by 1.4826) instead of mean + standard deviation as the baseline
against which deviations are scored. The reason is that real ECU signals are
rarely Gaussian: idle wobble, key-cycle steps, sensor stair-stepping, and the
very dropouts the analyzer is supposed to flag all skew a mean/stddev fit so
that the threshold drifts toward the outliers and loses sensitivity.

Median + MAD is robust to point contamination — a dropout below the noise
floor barely moves the median and does not move the MAD at all. The 1.4826
constant makes MAD a consistent estimator of stddev under a Gaussian model,
which keeps the `deviation_sigmas` parameter intuitive for callers who think
in stddevs.

A degenerate input (perfectly flat signal → MAD == 0) returns an empty list
rather than dividing by zero. This is documented in code and asserted in
`test_flat_signal_does_not_divide_by_zero`.

Trade-off: a slow ramp / drift will not be detected — that requires a windowed
baseline. We accept this for Phase 0-Lite and revisit in Phase 0 if needed.
