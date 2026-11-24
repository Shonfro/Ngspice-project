# Experiment design

The formal 20 ns dynamic-library batch uses paired blocks under fixed NGSPICE 47, C7552/SKY130 TT, KLU8, eight configuration threads, CPUs 0–15, monitor CPU 16, and Linux Default NUMA.

| Configuration | ELF | Allocator |
|---|---|---|
| A | original | system glibc |
| B | original | jemalloc |
| C | original | tcmalloc |
| D | BOLT | tcmalloc |

A→B tests jemalloc, A→C tests tcmalloc, C→D tests BOLT under tcmalloc, and A→D evaluates the complete endpoint. Four warmups and 28 formal runs are preserved in `result/dynamic_library/20ns/measurements/`; diagnostics are separate and never enter formal statistics.
