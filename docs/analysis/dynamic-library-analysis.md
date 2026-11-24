# Dynamic library analysis

The formal means are A 494.317 s, B 495.457 s, C 388.127 s, and D 380.069 s. A→C is 7/7 paired faster with a 95% CI that excludes zero; A→B does not support stable jemalloc benefit. C→D is faster on average, but its confidence interval crosses zero, so BOLT’s independent gain under tcmalloc is not statistically established.

Maps and loader records prove that jemalloc and tcmalloc entered the intended processes. Function profiles show BSIM4 load/assembly, `exp`/`log`, and KLU paths as major sampled locations. No allocator call/byte/thread-cache counters were collected; therefore lock contention, fragmentation, and syscall-reduction explanations remain hypotheses. See [`NGSPICE_Dynamic_Library_Complete_Analysis.md`](NGSPICE_Dynamic_Library_Complete_Analysis.md) for the complete evidence audit.
