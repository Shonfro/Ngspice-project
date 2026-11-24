# Integrated delivery acceptance

This directory is an integration record, not a new formal experiment report. The original delivery tree remains at its original paths; the new raw 20 ns dynamic-library batch is under `result/dynamic_library/20ns/measurements/`.

## Preserved historical baseline

- KLU8/Sparse8: 335.40 s / 653.85 s.
- Historical original/BOLT: 332.74 s / 329.86 s.
- Historical Default/Node 0: 332.30 s / 333.28 s.
- Existing enterprise/system report files were not edited.

## New batch acceptance

- 4 warmups and 28 formal runs are present.
- 32 `validation.json` files pass.
- 32 `/proc/PID/maps` snapshots are present.
- The formal CSV contains 28 rows.
- No historical 20 ns values are combined with the new A/D estimate.

| Comparison | Mean saving (s) | 95% paired CI (s) | Faster pairs |
|---|---:|---:|---:|
| A→B | -1.140 | [-10.740, 8.460] | 4/7 |
| A→C | 106.190 | [94.507, 117.873] | 7/7 |
| C→D | 8.059 | [-3.174, 19.292] | 5/7 |
| A→D | 114.249 | [102.815, 125.682] | 7/7 |

The same-batch final combination is A→D: 494.317 s to 380.069 s, 23.112% mean improvement. This is not added to the historical BOLT percentage. BOLT+glibc was not measured in this batch, so complete interaction separation is not claimed.

## Fingerprints

See `source/dependencies/allocators/SHA256SUMS` and each run's `execution.json`. The original and BOLT ELF fingerprints are also recorded in `design.json`, `execution.json`, and the baseline audit.

## Automated checks

Python `py_compile`, Shell `bash -n`, ELF/file checks, formal row count, validation count, maps count, and independent extraction checks are required before replacing the delivery archive. No formal 20 ns experiment is run by the packaging step.
