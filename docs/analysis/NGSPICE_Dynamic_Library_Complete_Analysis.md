# NGSPICE 动态库优化：完整数据审计与性能机制分析

## 1. 分析边界与证据规则

本文只分析 20 ns 正式动态库批次 A/B/C/D。正式结果来自既有逐次原始记录，未重新执行正式预热或 28 次正式运行，也未修改 `result/dynamic_library/20ns/` 中的数据。2 ns 探索性数据、历史 KLU8/BOLT/NUMA 批次和企业报告不并入本批次统计。

本轮新增的 `docs/analysis/diagnostics/` 是独立诊断证据。诊断 profile 用于观察计算热点和动态库加载，不用于替换或补充正式 Wall 均值；未采集到的 allocator 计数器不以推断值代替。

## 2. 配置、环境与动态库证据

固定条件为 NGSPICE 47、C7552/SKY130 TT、20 ns transient、KLU8、8 个配置线程、CPU 0–15、CPU 16 监控、Linux Default NUMA。四个正式配置如下：

| 配置 | ELF | 运行库 |
|---|---|---|
| A | 原始 NGSPICE | 系统 glibc |
| B | 原始 NGSPICE | jemalloc |
| C | 原始 NGSPICE | tcmalloc |
| D | BOLT NGSPICE | tcmalloc |

加载路径、映射和 SHA256 见 [`library-load-evidence.csv`](library-load-evidence.csv)。关键对象为：原始 ELF `303387...87be`，BOLT ELF `22f13d...4a5a`，jemalloc `70dd9d...bd961b`，tcmalloc `158b02...e9fc`。B 的 jemalloc、C/D 的 tcmalloc 均有实际进程映射证据；A 没有候选分配器映射。该证据确认“候选库实际加载”，但不等同于已经证明某类分配器内部路径占据了多少时间。

## 3. 正式性能结果

逐次数据、执行命令、`metrics.json`、`execution.json`、波形校验和 maps 索引见 [`run-inventory.csv`](run-inventory.csv)、[`performance-summary.csv`](performance-summary.csv) 及 `result/dynamic_library/20ns/measurements/`。

| 配置 | Wall 均值 (s) | CPU user 均值 (s) | CPU system 均值 (s) | RSS 均值 (KiB) | 波形 |
|---|---:|---:|---:|---:|---:|
| A | 494.317 | 998.347 | 53.797 | 899172.6 | 7/7 |
| B | 495.457 | 1008.190 | 56.443 | 899550.9 | 7/7 |
| C | 388.127 | 916.759 | 36.716 | 929833.7 | 7/7 |
| D | 380.069 | 891.677 | 37.739 | 930153.1 | 7/7 |

包含 4 次预热在内，波形校验为 32/32；正式运行是 28/28。配对区组结果为：

| 对比 | 平均节省 (s) | 95% CI (s) | 更快次数 |
|---|---:|---:|---:|
| A→B | -1.140 | [-10.740, 8.460] | 4/7 |
| A→C | 106.190 | [94.507, 117.873] | 7/7 |
| C→D | 8.059 | [-3.174, 19.292] | 5/7 |
| A→D | 114.249 | [102.815, 125.682] | 7/7 |

因此，tcmalloc 的 A→C 端到端收益在同批次内具有可重复证据；jemalloc 的 A→B 不支持稳定收益；D 相对 C 的平均优势为正但置信区间跨越 0，不能表述为已证明的 BOLT 独立收益。A→D 是完整组合的主要验收比较，不能把不同层次或不同批次百分比相加。

## 4. NGSPICE 内部阶段计时

阶段逐次数据见 [`phase-timing.csv`](phase-timing.csv)，均值见 [`phase-timing-summary.csv`](phase-timing-summary.csv)。

| 阶段均值 (s) | A | B | C | D |
|---|---:|---:|---:|---:|
| Transient analysis | 419.444 | 420.149 | 324.481 | 323.189 |
| Matrix load | 248.528 | 248.773 | 192.060 | 192.305 |
| Matrix solve | 19.242 | 19.223 | 18.997 | 19.055 |
| Total elapsed phase | 492.416 | 493.548 | 387.462 | 379.387 |

A→C 的同步变化主要出现在 Matrix/Transient load 或装配相关阶段，Matrix solve 均值基本不变；这与 tcmalloc 对实际计算路径有效的结论相容，但仅凭阶段计时不能证明具体 allocator 因果机制。C→D 没有显示 Matrix load 或 Matrix solve 的稳定下降。

源码审计见 [`ngspice-timing-source-evidence.md`](ngspice-timing-source-evidence.md)：资源字段由 NGSPICE 内部 `rusage`/phase 统计维护，`seconds()` 在 OpenMP 构建下使用 `omp_get_wtime()`，否则取决于源码编译分支。因此这些值是内部阶段统计，不是可简单相加的 Wall 分解；Matrix Load、Transient Load 等字段存在相关或包含关系。

## 5. 独立函数级诊断 profile

诊断命令、配置和原始采样位于 [`diagnostics/profiles-20ns/`](diagnostics/profiles-20ns/)。A/B/C/D 均生成了 `perf.data`、报告和脚本；四个报告的 `Total Lost Samples: 0`。D 使用了无调用图的 DSO/符号采样，因此 D 的报告不提供调用链；A/B/C 为较完整的符号采样。诊断运行的 Wall 不进入上面的正式均值。

主要采样热点（`perf report` overhead，不是函数 Wall 时间）如下：

| 配置 | 主要观察 |
|---|---|
| A | `BSIM4v5LoadOMP` 约 44.39% self；`exp`/`log`、`klu_refactor` 及 RHS/matrix 装配相关符号可见 |
| B | `BSIM4v5LoadOMP` 约 45.47% self；`exp`/`log`、`klu_refactor` 与 A 的热点结构相近 |
| C | `BSIM4v5LoadOMP` 约 48.51% self；`exp`/`log`、`klu_refactor` 与装配相关符号仍是主要路径 |
| D | `BSIM4v5LoadOMP` 49.18%；`exp` 6.96%、`BSIM4v5LoadRhsMat` 6.69%、`klu_refactor` 4.66%、`log` 3.78% |

A/B/C 的主要报告中没有 malloc/free 作为高占比顶层热点。该结果说明采样热点主要落在 BSIM4 负载、数学函数和 KLU 路径，不能说明 allocator 没有影响：分配可能发生在低占比函数、内联路径、未解析符号或短时路径中。现有 profile 也没有提供按调用点的分配字节数和调用次数。

## 6. 分配器机制证据与缺口

当前直接证据分级如下：

1. **成功构建**：私有 jemalloc/tcmalloc 构建产物和 SHA256 已在工程中保存。
2. **实际加载**：B、C、D 的 maps/加载日志确认候选库进入 NGSPICE 进程；A 为系统 glibc。
3. **可重复性能收益**：只有 tcmalloc 的 A→C 正式配对数据达到本批次可重复收益证据。jemalloc 未达到；BOLT+tcmalloc 相对 tcmalloc 的均值优势尚未达到独立稳定收益证据。

尚未采集到版本特定的 malloc/free 调用次数、分配字节数、峰值活跃分配、线程缓存命中率、锁等待或 `MallocExtension`/`mallctl` 快照。因此不能把 21.482% 收益归因于某个具体 tcmalloc 内部机制，也不能声称已经证明碎片、锁竞争或系统调用减少。后续若需要机制闭环，应在不改变正式数据的前提下另做受控 allocator instrumentation 批次。

## 7. BOLT 与动态库组合

本批次 C→D 为同一 tcmalloc 条件下的 BOLT 对照：平均节省 8.059 s、改善约 2.076%，但 95% CI 为 [-3.174, 19.292] s。阶段均值也没有显示稳定的 Matrix load/solve 降低。故本批次支持“D 端点最快”，不支持“BOLT 在 tcmalloc 上的独立收益已被统计证明”。本工程没有把缺失的 BOLT+glibc 同批次对照补写出来，也没有将历史 BOLT 的 0.86% 与本批次结果相加；若要完整估计交互效应，需要新增受控对照实验。

## 8. 历史基线审计

历史 KLU8 原版约 332.74 s 与本批次 A 的 494.317 s 不能直接比较。[`baseline-audit.md`](baseline-audit.md) 逐项记录了 ELF、输入闭包、线程/亲和性、NUMA、计时边界及系统负载证据；目前不能证明两批次完全同条件。因此本报告不解释该差异的单一原因，也不把 332.74→380.069 作为动态库收益。

## 9. 最终判断

在本批次和现有证据范围内，推荐验收配置为 **KLU8 + BOLT ELF + tcmalloc + Linux Default NUMA**，理由是 D 在同批次端到端 Wall 最低，且 A→D 的完整组合配对收益明确；其中 tcmalloc 的独立贡献由 A→C 直接支持。BOLT 是否保留仍应表述为工程端点选择，而不是已被 C→D 区间证明的稳定独立收益。jemalloc 不进入最终配置。

该推荐依赖以下边界：波形正确性已通过；动态库实际加载已确认；函数级 profile 已完成但不是正式性能统计；allocator 内部计数器和 BOLT 布局级因果证据仍缺失。

## 10. 复核入口

- 正式逐次记录：`result/dynamic_library/20ns/measurements/`
- 正式数据索引：[`run-inventory.csv`](run-inventory.csv)、[`data-inventory.csv`](data-inventory.csv)
- 统计与阶段数据：[`performance-summary.csv`](performance-summary.csv)、[`phase-timing.csv`](phase-timing.csv)、[`system-metrics.csv`](system-metrics.csv)
- 动态加载证据：[`library-load-evidence.csv`](library-load-evidence.csv)
- 函数诊断：`diagnostics/profiles-20ns/{A,B,C,D}/perf-report.txt`
- 缺口清单：[`gaps-and-next-steps.md`](gaps-and-next-steps.md)
