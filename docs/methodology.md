# Methodology

Formal results use seven paired blocks per configuration comparison. Warmups are retained but excluded from means. Wall, CPU time, RSS, faults, context switches, phase timing, maps, and waveform validation are retained per run. Paired savings are computed as left Wall minus right Wall, with a paired t confidence interval over seven blocks.

Evidence is separated into four levels: successful construction, actual process loading, repeatable formal performance, and internal mechanism evidence. `perf` overhead is sample overhead, not function Wall time. Phase fields are NGSPICE internal cumulative/resource statistics and must not be summed as an exact Wall decomposition.
