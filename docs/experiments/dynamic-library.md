# 动态库实验

固定条件为 NGSPICE 47、C7552/SKY130 TT、20 ns、KLU8、8 线程、CPU 0–15、监控 CPU 16、Linux Default NUMA。

正式数据位于 `result/dynamic_library/20ns/measurements/`。保留 4 次预热、28 次正式运行、32 次波形验证和 32 份 maps 快照。A 为原始 ELF+系统 glibc，B 为原始 ELF+jemalloc，C 为原始 ELF+tcmalloc，D 为 BOLT ELF+tcmalloc。

整理验收不启动正式仿真。需运行实验时，先检查兼容性，再显式执行 `python3 -u code/dynamic_library/run_20ns.py`；脚本通过 `code/core/experiment.py` 复用核心模拟逻辑，并通过 `NGSPICE_JEMALLOC`、`NGSPICE_TCMALLOC` 和 `NGSPICE_BOLT_ELF` 支持路径覆盖。不得替换系统 glibc/libm。
