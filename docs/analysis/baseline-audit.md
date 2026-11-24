# 旧基线与 20 ns 动态库基线审计

本批次唯一可重算的正式基线是 A：原始 ELF + 系统 glibc，7 次 Wall 均值由 `run-inventory.csv` 重新计算为 494.317 s。历史报告中的 KLU8 原版约 332.74 s 仅作为外部参考。

| 审计项 | 当前批次证据 | 旧批次证据 | 判断 |
|---|---|---|---|
| NGSPICE ELF SHA256/Build ID | `execution.json`、`design.json` | 可见部分历史材料 | 未证明一致 |
| 网表/模型闭包 | 每次 `inputs.json`、`data-inventory.csv` | 历史归档材料 | 未证明逐项一致 |
| KLU8/线程 | execution profile，8 threads | 报告摘要 | 描述一致，底层证据不足 |
| 20 ns 仿真窗口 | execution/validation | 报告摘要 | 描述一致，边界未完全证明 |
| CPU affinity/NUMA | execution 与设计记录 | 历史摘要 | 新批次可确认，旧批次不足 |
| 计时起止边界 | `time.tsv`/metrics | 历史 runner 记录 | 未证明一致 |
| 系统负载/频率 | 无完整可比快照 | 不完整 | 无法确认 |

结论：不能把 332.74→494.317 或 332.74→380.069 作为本次优化收益，也不能把历史 BOLT 0.86% 与本批次 tcmalloc 收益直接相加。当前可支持的端到端结论仅来自本批次 A→B/C/D 配对数据。
