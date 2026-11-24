# 优化总结

历史应用层、软件层和系统层结果继续保留在 `result/`。动态库正式批次中，tcmalloc 相对 A 有明确同批次收益；jemalloc 未显示稳定收益；D 相对 C 的平均改善为正，但配对置信区间跨越 0。最终候选配置记录为 KLU8 + BOLT ELF + tcmalloc + Linux Default NUMA，依据为同一批次 A/D 数据。
