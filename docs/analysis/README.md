# 20 ns 动态库数据审计资料

本目录只包含对既有 20 ns 动态库正式批次的只读提取、重算和分析，不包含新实验结果。原始证据仍保留在 `result/dynamic_library/20ns/measurements/`，本目录中的 CSV 和 Markdown 均可由 `scripts/audit_data.py`、`scripts/analyze_performance.py` 重新生成。

主要文件：

- `run-inventory.csv`：28 次正式运行逐次指标及证据路径。
- `data-inventory.csv`：关键原始文件大小和 SHA256。
- `performance-summary.csv`：独立重算的配对统计。
- `phase-timing.csv`、`phase-timing-summary.csv`：NGSPICE 阶段计时逐次值和汇总。
- `system-metrics.csv`：CPU、RSS、缺页和上下文切换。
- `function-profile.csv`、`allocator-statistics.csv`：明确标记未采集的机制指标。
- `metric-availability.csv`：数据可用性和证据边界。
- `analysis-report.md`：正式分析报告。
- `baseline-audit.md`：旧 332.74 s 基线与新批次的差异审计。
- `gaps-and-next-steps.md`：数据缺口及后续最小补采方案。

重新生成：

```bash
python3 docs/analysis/scripts/audit_data.py
python3 docs/analysis/scripts/analyze_performance.py
```

脚本只读取原始文件并覆盖本目录派生资料，不修改 `result/`、`raw/`、`intermediate/` 或实验控制代码，也不会启动 NGSPICE。
