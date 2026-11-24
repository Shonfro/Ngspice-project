# 证据索引

| 证据 | 位置 | 用途 |
|---|---|---|
| 设计与固定条件 | `result/dynamic_library/20ns/measurements/design.json` | 配置、线程、CPU、NUMA、执行顺序 |
| 正式逐次指标 | `result/dynamic_library/20ns/measurements/formal-20ns/formal/block-*/{A,B,C,D}/metrics.json` | Wall、CPU、RSS、缺页、上下文切换、阶段计时 |
| 执行环境 | 同目录 `execution.json` | ELF、SHA256、LD_PRELOAD、solver、线程、监控 CPU |
| 正确性 | 同目录 `validation.json` | 波形和求解器正确性 |
| 动态库映射 | 同目录 `maps.txt` | `/proc/PID/maps` 映射证据 |
| 控制台与计时 | 同目录 `console.log`、`time.tsv` | 原始运行输出和 GNU time 证据 |
| 汇总核对 | `formal-20ns/summary.json` | 与独立脚本重算结果交叉核对 |
| 私有库哈希 | `source/dependencies/allocators/SHA256SUMS` | jemalloc/tcmalloc 文件完整性 |

`metrics.json` 和 `execution.json` 中保留的历史绝对路径是原始运行 provenance，不被本次审计改写；当前工程内的文件路径、大小和 SHA256 以 `data-inventory.csv` 为准。
