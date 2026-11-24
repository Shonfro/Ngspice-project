# NGSPICE 动态库实验数据审计与性能机制分析
## 分析边界
本报告只分析 20 ns 正式批次的 A/B/C/D 配置；预热不进入统计，2 ns、历史批次和其他实验不混入计算。本轮未执行仿真、未修改原始证据。
## 直接测量与重算结果
正式运行清单见 `run-inventory.csv`，每行包含原始 `metrics.json`、`execution.json`、`validation.json` 和 `maps.txt` 路径。独立重算的均值如下：

| 配置 | Wall mean (s) | CPU user mean (s) | CPU system mean (s) | RSS mean (KiB) | 波形 |
|---|---:|---:|---:|---:|---:|
| A | 494.317 | 998.347 | 53.797 | 899172.6 | 7/7 |
| B | 495.457 | 1008.190 | 56.443 | 899550.9 | 7/7 |
| C | 388.127 | 916.759 | 36.716 | 929833.7 | 7/7 |
| D | 380.069 | 891.677 | 37.739 | 930153.1 | 7/7 |

正式波形校验为 28/28；连同 4 次预热共 32/32 份校验文件。配对统计见 `performance-summary.csv`。Wall 改善以同一区组的左配置 Wall 减右配置 Wall 计算，95% CI 为 df=6 的配对 t 区间。

| 对比 | 平均节省 (s) | 95% CI (s) | 更快次数 |
|---|---:|---:|---:|
| A->B | -1.140 | [-10.740, 8.460] | 4/7 |
| A->C | 106.190 | [94.507, 117.873] | 7/7 |
| C->D | 8.059 | [-3.174, 19.292] | 5/7 |
| A->D | 114.249 | [102.815, 125.682] | 7/7 |

## NGSPICE 阶段耗时

阶段计时直接来自每次 `metrics.json` 的 `phases` 字段，不由 Wall Time 推算。完整逐次数据见 `phase-timing.csv`，配置均值和标准差见 `phase-timing-summary.csv`。关键均值如下（秒）：

| 阶段 | A glibc | B jemalloc | C tcmalloc | D BOLT+tcmalloc |
|---|---:|---:|---:|---:|
| Transient analysis time | 419.444 | 420.149 | 324.481 | 323.189 |
| Matrix load time | 248.528 | 248.773 | 192.060 | 192.305 |
| Matrix solve time | 19.242 | 19.223 | 18.997 | 19.055 |
| Total elapsed time (seconds) | 492.416 | 493.548 | 387.462 | 379.387 |

现有证据显示可直接比较 Matrix load/factor/solve、Transient load/solve/analysis 等阶段；这些阶段的具体变化应以表格为准。由于阶段计时是进程内统计且 Wall/阶段计时的边界不同，不将各阶段简单相加作为因果分解。
从均值看，A→C 的主要同步变化出现在 Matrix/Transient load（约 248.528→192.060 s、248.112→191.745 s），而 Matrix solve 约 19.242→18.997 s，变化很小；这支持“tcmalloc 影响更接近装配/加载路径、不是求解器核心耗时”的相关性描述，但不能单凭阶段计时证明分配器因果机制。C→D 的 Matrix load 约 192.060→192.305 s、Matrix solve 约 18.997→19.055 s，未显示阶段级明显下降；BOLT 的额外收益不能在本批次中分解为某个阶段的稳定独立贡献。

## Profile 与分配器机制证据

保存的数据中未发现 A/B/C/D 的 `perf.data`、perf report、perf script 或调用栈文件；因此不能报告函数级 Self CPU、Children CPU、样本百分比，也不能判断 tcmalloc 是否降低了某个具体分配器函数的采样开销。

未发现版本特定的 malloc/free 调用计数、分配字节数、线程缓存或 MallocExtension/mallctl 统计。`library-load-evidence.csv` 汇总了 maps/LD_DEBUG 证据：B 的 jemalloc、C/D 的 tcmalloc 均有实际加载记录；A 的 maps 显示系统 libc，且无候选分配器映射。加载证据不能证明锁竞争、碎片或系统调用减少。

## 结论

A→C 的同批次 Wall 改善有 7/7 配对更快且区间不跨 0，支持 tcmalloc 在本实验路径上具有可重复端到端收益。A→B 的区间跨 0，不支持 jemalloc 有稳定收益。C→D 平均为正，但区间跨 0，因此只能说本批次 BOLT+tcmalloc 比 tcmalloc 更快，不能据此证明 BOLT 在 tcmalloc 基础上具有稳定独立收益。由于缺少函数级 Profile 和 allocator counters，tcmalloc 的具体机制仍是待验证假设，而不是本批次直接测量结论。

旧版 332.74 s 与本批次 A 的差异审计见 `baseline-audit.md`。现有材料不能逐项证明 ELF、输入闭包、计时边界、系统负载和动态链接环境完全一致，因此不选择猜测性原因，也不叠加两个批次的收益。
