# Environment

The formal batch target is NGSPICE 47 on Hygon C86-4G / OPN 7490, C7552/SKY130 TT, Linux Default NUMA, KLU8 and eight configuration threads. CPU affinity is 0–15 with CPU 16 reserved for monitoring. The project stores private allocator sources/build records and loader evidence; it never replaces system glibc or libm.

Exact per-run executable, input, dependency and affinity records are authoritative in each preserved `execution.json`, `inputs.json`, `design.json`, and `maps.txt`. The historical 332.74 s baseline is not assumed equivalent to the current 494.317 s A batch.
