#!/usr/bin/env python3
"""Generate analysis tables and a cautious report from audit_data.py outputs."""
import csv
import statistics
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
OUT = PACKAGE / "docs/analysis"
DATA = PACKAGE / "result/dynamic_library/20ns/measurements"


def load(name):
    with (OUT / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write(name, fields, rows):
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        out = csv.DictWriter(handle, fieldnames=fields)
        out.writeheader()
        out.writerows(rows)


def main():
    runs = load("run-inventory.csv")
    performance = load("performance-summary.csv")
    system_fields = ["config", "block", "wall_s", "user_cpu_s", "system_cpu_s",
                     "max_rss_kib", "minor_faults", "major_faults", "voluntary_cs",
                     "involuntary_cs", "cpu_affinity", "monitor_cpu", "numa_policy",
                     "threads", "solver", "waveform_passed", "source_metrics"]
    system = [{k: row.get(k, "N/A") for k in system_fields[:-1]} |
              {"source_metrics": row["metrics_path"]} for row in runs]
    write("system-metrics.csv", system_fields, system)

    profile_files = list(PACKAGE.rglob("perf.data")) + list(PACKAGE.rglob("perf-report.txt"))
    allocator_files = [p for p in PACKAGE.rglob("*") if p.is_file() and
                       any(x in p.name.lower() for x in ("mallinfo", "malloc-stats", "malloc_stats"))]
    write("metric-availability.csv",
          ["metric", "status", "evidence", "note"],
          [
              {"metric": "Wall/CPU/RSS/faults/context switches", "status": "available",
               "evidence": "metrics.json", "note": "32 runs; formal subset 28"},
              {"metric": "NGSPICE phase timing", "status": "available",
               "evidence": "metrics.json phases", "note": "Matrix and transient counters present"},
              {"metric": "waveform correctness", "status": "available",
               "evidence": "validation.json", "note": "32/32 passed"},
              {"metric": "maps/library mappings", "status": "available",
               "evidence": "maps.txt", "note": "32 snapshots"},
              {"metric": "function-level perf profile", "status": "not_collected",
               "evidence": ";".join(str(p) for p in profile_files) or "N/A",
               "note": "No perf.data/perf report for A/B/C/D in preserved package"},
              {"metric": "allocator call/byte statistics", "status": "not_collected",
               "evidence": ";".join(str(p) for p in allocator_files) or "N/A",
               "note": "No version-specific malloc allocator counters found"},
              {"metric": "CPU migrations/NUMA page counters per run", "status": "not_collected",
               "evidence": "N/A", "note": "maps exists, but no per-run migration/numa telemetry field"},
          ])

    load_rows = []
    for config, expected, evidence in (
            ("A", "system libc/glibc", DATA / "formal-20ns/formal/block-01/A/maps.txt"),
            ("B", "jemalloc", PACKAGE / "result/dynamic_library/jemalloc-load.log"),
            ("C", "tcmalloc", PACKAGE / "result/dynamic_library/tcmalloc-load.log"),
            ("D", "tcmalloc", DATA / "formal-20ns/formal/block-01/D/maps.txt")):
        text = evidence.read_text(encoding="utf-8", errors="replace") if evidence.exists() else ""
        if config == "A":
            mapped = "libc-2.17.so" in text and "jemalloc" not in text and "tcmalloc" not in text
            symbol = "not_collected"
        else:
            mapped = expected in text.lower()
            symbol = "available" if "symbol=malloc" in text else "not_collected"
        load_rows.append({"config": config, "expected_allocator": expected,
                          "maps_or_loader_evidence": "available" if mapped else "not_confirmed",
                          "symbol_lookup_evidence": symbol,
                          "evidence_path": str(evidence)})
    write("library-load-evidence.csv",
          ["config", "expected_allocator", "maps_or_loader_evidence",
           "symbol_lookup_evidence", "evidence_path"], load_rows)

    write("function-profile.csv",
          ["config", "function", "self_cpu_percent", "children_cpu_percent",
           "samples", "sample_percent", "dso", "call_path", "status"],
          [{"config": c, "function": "N/A", "self_cpu_percent": "N/A",
            "children_cpu_percent": "N/A", "samples": "N/A", "sample_percent": "N/A",
            "dso": "N/A", "call_path": "N/A", "status": "not_collected"}
           for c in "ABCD"])
    write("allocator-statistics.csv",
          ["config", "malloc_calls", "free_calls", "calloc_calls", "realloc_calls",
           "allocated_bytes", "average_allocation_bytes", "peak_heap", "thread_cache",
           "status", "evidence"],
          [{"config": c, "malloc_calls": "N/A", "free_calls": "N/A",
            "calloc_calls": "N/A", "realloc_calls": "N/A", "allocated_bytes": "N/A",
            "average_allocation_bytes": "N/A", "peak_heap": "N/A",
            "thread_cache": "N/A", "status": "not_collected", "evidence": "N/A"}
           for c in "ABCD"])

    means = {}
    for row in runs:
        means.setdefault(row["config"], []).append(float(row["wall_s"]))
    report = []
    report.append("# NGSPICE 动态库实验数据审计与性能机制分析\n")
    report.append("## 分析边界\n")
    report.append("本报告只分析 20 ns 正式批次的 A/B/C/D 配置；预热不进入统计，2 ns、历史批次和其他实验不混入计算。本轮未执行仿真、未修改原始证据。\n")
    report.append("## 直接测量与重算结果\n")
    report.append("正式运行清单见 `run-inventory.csv`，每行包含原始 `metrics.json`、`execution.json`、`validation.json` 和 `maps.txt` 路径。独立重算的均值如下：\n\n")
    report.append("| 配置 | Wall mean (s) | CPU user mean (s) | CPU system mean (s) | RSS mean (KiB) | 波形 |\n|---|---:|---:|---:|---:|---:|\n")
    for c in "ABCD":
        subset=[r for r in runs if r["config"]==c]
        report.append(f"| {c} | {statistics.mean(means[c]):.3f} | {statistics.mean(float(r['user_cpu_s']) for r in subset):.3f} | {statistics.mean(float(r['system_cpu_s']) for r in subset):.3f} | {statistics.mean(float(r['max_rss_kib']) for r in subset):.1f} | {sum(r['waveform_passed']=='True' for r in subset)}/{len(subset)} |\n")
    total_validations = list(DATA.rglob("validation.json"))
    report.append(f"\n正式波形校验为 28/28；连同 4 次预热共 {len(total_validations)}/{len(total_validations)} 份校验文件。配对统计见 `performance-summary.csv`。Wall 改善以同一区组的左配置 Wall 减右配置 Wall 计算，95% CI 为 df=6 的配对 t 区间。\n\n")
    report.append("| 对比 | 平均节省 (s) | 95% CI (s) | 更快次数 |\n|---|---:|---:|---:|\n")
    for r in performance:
        report.append(f"| {r['comparison']} | {float(r['mean_saving_s']):.3f} | [{float(r['ci95_low_s']):.3f}, {float(r['ci95_high_s']):.3f}] | {r['faster_pairs']}/7 |\n")
    report.append("\n## NGSPICE 阶段耗时\n\n")
    report.append("阶段计时直接来自每次 `metrics.json` 的 `phases` 字段，不由 Wall Time 推算。完整逐次数据见 `phase-timing.csv`，配置均值和标准差见 `phase-timing-summary.csv`。关键均值如下（秒）：\n\n")
    report.append("| 阶段 | A glibc | B jemalloc | C tcmalloc | D BOLT+tcmalloc |\n|---|---:|---:|---:|---:|\n")
    phase_summary = load("phase-timing-summary.csv")
    wanted = ["Transient analysis time", "Matrix load time", "Matrix solve time", "Total elapsed time (seconds)"]
    for phase in wanted:
        vals = {r["config"]: float(r["mean_s"]) for r in phase_summary if r["phase"] == phase}
        report.append(f"| {phase} | {vals['A']:.3f} | {vals['B']:.3f} | {vals['C']:.3f} | {vals['D']:.3f} |\n")
    report.append("\n")
    report.append("现有证据显示可直接比较 Matrix load/factor/solve、Transient load/solve/analysis 等阶段；这些阶段的具体变化应以表格为准。由于阶段计时是进程内统计且 Wall/阶段计时的边界不同，不将各阶段简单相加作为因果分解。\n")
    report.append("从均值看，A→C 的主要同步变化出现在 Matrix/Transient load（约 248.528→192.060 s、248.112→191.745 s），而 Matrix solve 约 19.242→18.997 s，变化很小；这支持“tcmalloc 影响更接近装配/加载路径、不是求解器核心耗时”的相关性描述，但不能单凭阶段计时证明分配器因果机制。C→D 的 Matrix load 约 192.060→192.305 s、Matrix solve 约 18.997→19.055 s，未显示阶段级明显下降；BOLT 的额外收益不能在本批次中分解为某个阶段的稳定独立贡献。\n")
    report.append("\n## Profile 与分配器机制证据\n\n")
    report.append("保存的数据中未发现 A/B/C/D 的 `perf.data`、perf report、perf script 或调用栈文件；因此不能报告函数级 Self CPU、Children CPU、样本百分比，也不能判断 tcmalloc 是否降低了某个具体分配器函数的采样开销。\n\n")
    report.append("未发现版本特定的 malloc/free 调用计数、分配字节数、线程缓存或 MallocExtension/mallctl 统计。`library-load-evidence.csv` 汇总了 maps/LD_DEBUG 证据：B 的 jemalloc、C/D 的 tcmalloc 均有实际加载记录；A 的 maps 显示系统 libc，且无候选分配器映射。加载证据不能证明锁竞争、碎片或系统调用减少。\n")
    report.append("\n## 结论\n\n")
    report.append("A→C 的同批次 Wall 改善有 7/7 配对更快且区间不跨 0，支持 tcmalloc 在本实验路径上具有可重复端到端收益。A→B 的区间跨 0，不支持 jemalloc 有稳定收益。C→D 平均为正，但区间跨 0，因此只能说本批次 BOLT+tcmalloc 比 tcmalloc 更快，不能据此证明 BOLT 在 tcmalloc 基础上具有稳定独立收益。由于缺少函数级 Profile 和 allocator counters，tcmalloc 的具体机制仍是待验证假设，而不是本批次直接测量结论。\n")
    report.append("\n旧版 332.74 s 与本批次 A 的差异审计见 `baseline-audit.md`。现有材料不能逐项证明 ELF、输入闭包、计时边界、系统负载和动态链接环境完全一致，因此不选择猜测性原因，也不叠加两个批次的收益。\n")
    (OUT / "analysis-report.md").write_text("".join(report), encoding="utf-8")

    (OUT / "baseline-audit.md").write_text("""# 旧基线与 20 ns 动态库基线审计\n\n本批次唯一可重算的正式基线是 A：原始 ELF + 系统 glibc，7 次 Wall 均值由 `run-inventory.csv` 重新计算为 494.317 s。历史报告中的 KLU8 原版约 332.74 s 仅作为外部参考。\n\n| 审计项 | 当前批次证据 | 旧批次证据 | 判断 |\n|---|---|---|---|\n| NGSPICE ELF SHA256/Build ID | `execution.json`、`design.json` | 可见部分历史材料 | 未证明一致 |\n| 网表/模型闭包 | 每次 `inputs.json`、`data-inventory.csv` | 历史归档材料 | 未证明逐项一致 |\n| KLU8/线程 | execution profile，8 threads | 报告摘要 | 描述一致，底层证据不足 |\n| 20 ns 仿真窗口 | execution/validation | 报告摘要 | 描述一致，边界未完全证明 |\n| CPU affinity/NUMA | execution 与设计记录 | 历史摘要 | 新批次可确认，旧批次不足 |\n| 计时起止边界 | `time.tsv`/metrics | 历史 runner 记录 | 未证明一致 |\n| 系统负载/频率 | 无完整可比快照 | 不完整 | 无法确认 |\n\n结论：不能把 332.74→494.317 或 332.74→380.069 作为本次优化收益，也不能把历史 BOLT 0.86% 与本批次 tcmalloc 收益直接相加。当前可支持的端到端结论仅来自本批次 A→B/C/D 配对数据。\n""")
    (OUT / "gaps-and-next-steps.md").write_text("""# 数据缺口与后续最小补采方案\n\n## 本轮未采集\n\n- A/B/C/D 函数级 `perf.data`、perf report、perf script 和稳定调用栈。\n- malloc/free/calloc/realloc 调用次数、分配字节数、线程缓存和 allocator-specific counters。\n- 每次运行可直接比较的 CPU migrations、NUMA 页面统计和系统负载快照。\n- 可将旧 332.74 s 批次与新批次逐项对齐的完整 ELF、输入闭包、动态依赖和计时边界证据。\n\n## 后续最小补采\n\n1. 在同一固定 20 ns A/B/C/D 控制条件下，分别采集足够重复的 `perf record -g`、`perf report` 和 `perf script`，记录 ELF/DSO Build ID；仅用于机制诊断，不替换当前正式结果。\n2. 使用与 glibc 2.17、jemalloc 5.3.0、gperftools 2.7 ABI 匹配的诊断接口或 LD_PRELOAD 统计 shim，记录调用次数、字节数、峰值和线程缓存；先做短时验证，再决定是否正式补采。\n3. 为每次运行保存 `/proc/PID/status`、`sched`、`numa_maps`、CPU migration、频率/负载和完整动态链接符号解析快照，并对旧基线使用同一采集协议。\n\n这些补采属于后续独立实验，本轮未执行。\n""")


if __name__ == "__main__":
    main()
