"""Rebuild descriptive statistics and dependency-free charts from raw run records."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
from statistics import median

from .inputs import json_write, sha256

COLORS = ["#475569", "#2563eb", "#0891b2", "#059669", "#d97706", "#7c3aed"]


def aggregate(campaign: dict, records: list[dict]) -> list[dict]:
    config = campaign["config"]
    groups = []
    for stop in config.get("stop_times_ns", [None]):
        for profile in config["profiles"]:
            selected = [r for r in records if r["kind"] == "measurement"
                        and r["stop_ns"] == stop and r["profile"]["name"] == profile["name"]]
            valid = [r for r in selected if r["status"] == "succeeded"
                     and r.get("validation", {}).get("passed")
                     and r.get("binding_audit", {}).get("passed")]
            completed = [r for r in selected if r in valid or (
                r["status"] == "validation_failed" and r.get("returncode") == 0
                and r.get("binding_audit", {}).get("passed")
                and r.get("validation", {}).get("reached_stop")
                and r.get("validation", {}).get("finite")
                and "comparison" in r.get("validation", {})
                and not r.get("thread_count_mismatch"))]
            group = {"stop_ns": stop, "profile": profile["name"], "role": profile["role"],
                     "requested_threads": profile["threads"], "expected_repeats": config["repeats"],
                     "recorded": len(selected), "accepted": len(valid), "completed": len(completed),
                     "failures": [{"run": Path(r["run"]).name, "status": r["status"]}
                                  for r in selected if r not in valid],
                     "complete": len(valid) == len(selected) == config["repeats"]}
            for field in ("wall_seconds", "cpu_seconds", "max_rss_kib"):
                for prefix, subset in (("", valid), ("completed_", completed)):
                    values = [r["resources"][field] for r in subset
                              if isinstance(r.get("resources", {}).get(field), (int, float))
                              and math.isfinite(r["resources"][field])]
                    group[prefix + field] = ({"median": median(values), "min": min(values), "max": max(values)}
                                             if values else None)
            groups.append(group)
    for group in groups:
        baseline = next(g for g in groups if g["stop_ns"] == group["stop_ns"] and g["role"] == "baseline")
        wall, base = group["wall_seconds"], baseline["wall_seconds"]
        group["speedup"] = (base["median"] / wall["median"]
                            if group["complete"] and baseline["complete"] and wall and base
                            and wall["median"] > 0 and base["median"] > 0 else None)
        group["cpu_cost_ratio"] = (group["cpu_seconds"]["median"] / baseline["cpu_seconds"]["median"]
                                   if group["speedup"] is not None and group["cpu_seconds"]
                                   and baseline["cpu_seconds"] else None)
    return groups


def chart(groups: list[dict], config: dict, field: str, title: str, unit: str) -> str:
    stops = config.get("stop_times_ns", [None])
    columns = min(2, len(stops))
    rows = math.ceil(len(stops) / columns)
    panel_height = max(285, 90 + len(config["profiles"]) * 37)
    width, height = 1280, 136 + rows * panel_height + 64
    panel_width = (width - 72) / columns
    whiskers = ("Whiskers: fixed baseline median / configuration max–min; not confidence intervals."
                if field == "speedup" else "Bars: median; whiskers: observed min–max (not confidence intervals).")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
           f'<title>{escape(title)}</title>',
           '<defs><pattern id="unqualified" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#f1f5f9"/><path d="M-2 2L2-2M0 8L8 0M6 10L10 6" stroke="#94a3b8"/></pattern></defs>',
           '<rect width="100%" height="100%" fill="#f8fafc"/>',
           '<g font-family="DejaVu Sans,sans-serif" fill="#0f172a">',
           f'<text x="36" y="39" font-size="25" font-weight="bold">{escape(title)}</text>',
           f'<text x="36" y="68" font-size="14" fill="#475569">{escape(config["case"])} · CPU / NUMA audited · see report for frequency controls · warmups excluded</text>',
           f'<text x="36" y="91" font-size="13" fill="#475569">{escape(whiskers)} Each panel has its own x-axis.</text>',
           '<text x="36" y="113" font-size="13" fill="#92400e">Hatched / †: completed timings without a fully qualified group; not an accepted speedup. (n/N) = qualified/planned.</text>']
    for index, stop in enumerate(stops):
        left = 36 + index % columns * panel_width
        top = 136 + index // columns * panel_height
        entries = [g for g in groups if g["stop_ns"] == stop]
        baseline = next(g for g in entries if g["role"] == "baseline")
        values = []
        for group in entries:
            item = group.get(field)
            diagnostic = field != "speedup" and not group["complete"]
            if diagnostic:
                item = group.get("completed_" + field)
            if field == "speedup":
                value = item
                low = baseline["wall_seconds"]["median"] / group["wall_seconds"]["max"] if value is not None else 0
                high = baseline["wall_seconds"]["median"] / group["wall_seconds"]["min"] if value is not None else 0
                if group["role"] == "baseline" and value is not None:
                    low = high = value
            else:
                value, low, high = (item["median"], item["min"], item["max"]) if item else (None, 0, 0)
            values.append((value, low, high, diagnostic))
        maximum = max([v[2] for v in values] + [1]) * 1.22
        x0, plot_width = left + 141, panel_width - 198
        label = f"{stop:g} ns" if stop is not None else "Full flow"
        out.append(f'<text x="{left}" y="{top + 23}" font-size="20" font-weight="bold">{label}</text>')
        for tick in range(5):
            value = maximum * tick / 4
            x = x0 + plot_width * tick / 4
            y2 = top + 54 + len(entries) * 37
            out += [f'<line x1="{x:.1f}" y1="{top + 42}" x2="{x:.1f}" y2="{y2}" stroke="#e2e8f0"/>',
                    f'<text x="{x:.1f}" y="{y2 + 22}" text-anchor="middle" font-size="12" fill="#64748b">{value:.1f}</text>']
        for row, (group, values_row) in enumerate(zip(entries, values)):
            value, low, high, diagnostic = values_row
            y = top + 55 + row * 37
            profile_label = group["profile"] + (f' ({group["accepted"]}/{group["expected_repeats"]})' if not group["complete"] else "")
            out.append(f'<text x="{x0 - 12}" y="{y + 5}" text-anchor="end" font-size="14">{escape(profile_label)}</text>')
            if value is None:
                out.append(f'<text x="{x0 + 8}" y="{y + 5}" font-size="13" fill="#b91c1c">No qualified data</text>')
                continue
            length = value / maximum * plot_width
            lo, hi = x0 + low / maximum * plot_width, x0 + high / maximum * plot_width
            color = COLORS[row % len(COLORS)]
            fill = "url(#unqualified)" if diagnostic else color
            marker = " †" if diagnostic else ""
            out += [f'<rect x="{x0}" y="{y - 11}" width="{length:.2f}" height="23" rx="3" fill="{fill}" stroke="{color}"/>',
                    f'<path d="M {lo:.2f} {y} H {hi:.2f} M {lo:.2f} {y - 6} V {y + 6} M {hi:.2f} {y - 6} V {y + 6}" stroke="#0f172a" fill="none" stroke-width="1.5"/>',
                    f'<text x="{max(hi, x0 + length) + 8:.2f}" y="{y + 5}" font-size="13" font-weight="bold">{value:.2f}{escape(unit)}{marker}</text>']
        out.append(f'<text x="{x0 + plot_width}" y="{top + panel_height - 10}" text-anchor="end" font-size="12" fill="#475569">{escape(unit or field)}</text>')
    out += [f'<text x="36" y="{height - 25}" font-size="13" fill="#64748b">N={config["repeats"]} measured repetitions per configuration. Speedup = same-endpoint baseline median / configuration median.</text>',
            '</g></svg>']
    return "\n".join(out)


def generate(destination: Path) -> dict:
    destination = destination.resolve()
    campaign = json.loads((destination / "campaign.json").read_text())
    config = campaign["config"]
    records = [json.loads((destination / item["directory"] / "run.json").read_text())
               for item in campaign["runs"]]
    groups = aggregate(campaign, records)
    output = destination / "report"
    output.mkdir(exist_ok=True)
    comparisons = [r["validation"]["comparison"] for r in records if "comparison" in r.get("validation", {})]
    differences = [c.get("maximum_absolute_printed_difference", c.get("maximum_absolute_difference"))
                   for c in comparisons]
    platforms = sorted({
        f'{model}; {host["kernel"].get("sysname", "")} {host["kernel"].get("release", "")} {host["kernel"].get("machine", "")}'
        for record in records for host in [record.get("host_before", {})] if "kernel" in host
        for model in {cpu.get("model_name_reported", "unknown CPU")
                      for cpu in host.get("frequency_reported", {}).values()}})
    findings = []
    for stop in config.get("stop_times_ns", [None]):
        label = f"{stop:g} ns" if stop is not None else "完整流程"
        qualified = [group for group in groups if group["stop_ns"] == stop and group["speedup"] is not None]
        if qualified:
            best = min(qualified, key=lambda group: group["wall_seconds"]["median"])
            findings.append(f'{label}：本轮候选中最快的合格配置为 {best["profile"]}，'
                            f'{best["wall_seconds"]["median"]:.2f} 秒，较同终止时间基线 {best["speedup"]:.3f}×。')
        else:
            findings.append(f"{label}：尚无满足完整重复数的合格对照，不作速度结论。")
    summary = {"schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
               "campaign_status": campaign["status"], "tool": config["tool"], "case": config["case"],
               "scope": config["baseline_scope"], "groups": groups, "findings": findings,
               "platforms": platforms,
               "tool_identities": list({json.dumps(r["tool_identity"], sort_keys=True): r["tool_identity"]
                                        for r in records if r.get("tool_identity")}.values()),
               "warmups": sum(r["kind"] == "warmup" for r in records),
               "measurements": sum(r["kind"] == "measurement" for r in records),
               "failed_runs": [Path(r["run"]).name for r in records if r["status"] != "succeeded"],
               "numerical_comparisons": {
                   "count": len(comparisons), "passed": sum(c.get("passed", False) for c in comparisons),
                   "maximum_saved_voltage_difference_v": max((d for d in differences if d is not None), default=None)},
               "frequency_locked": bool(records) and all(r.get("frequency_control", {}).get("locked") for r in records),
               "statistics": "Median and observed min/max, not confidence intervals. No outliers removed. Warmups excluded."}
    json_write(output / "summary.json", summary)
    with (output / "runs.csv").open("w", newline="") as stream:
        statistics_names = ["license_checkout_seconds", "readin_reported_seconds", "op_point_reported_seconds",
                            "transient_reported_seconds", "transient_points", "transient_iterations"]
        names = ["run", "kind", "stop_ns", "profile", "status", "wall_seconds", "cpu_seconds",
                 "max_rss_kib", "actual_threads", "last_time_s", "binding_passed", "max_printed_difference_v",
                 *statistics_names, "numerical_passed", "samples_outside_tolerance", "identical_printed_values"]
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        for record in records:
            valid = record.get("validation", {})
            writer.writerow({"run": Path(record["run"]).name, "kind": record["kind"],
                             "stop_ns": record["stop_ns"], "profile": record["profile"]["name"],
                             "status": record["status"],
                             **{k: record.get("resources", {}).get(k) for k in names[5:8]},
                             "actual_threads": valid.get("simulator_statistics", {}).get("actual_threads"),
                             "last_time_s": valid.get("last_time"),
                             "binding_passed": record.get("binding_audit", {}).get("passed", False),
                             **{k: valid.get("simulator_statistics", {}).get(k) for k in statistics_names},
                             "max_printed_difference_v": valid.get("comparison", {}).get("maximum_absolute_printed_difference"),
                             "numerical_passed": valid.get("comparison", {}).get("passed"),
                             "samples_outside_tolerance": valid.get("comparison", {}).get("samples_outside_tolerance"),
                             "identical_printed_values": valid.get("comparison", {}).get("identical_printed_values")})
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["stop_ns", "profile", "accepted", "expected", "wall_median_s", "wall_min_s", "wall_max_s", "speedup", "cpu_median_s", "cpu_cost_ratio", "peak_individual_rss_median_mib",
                         "completed", "completed_wall_median_s", "completed_wall_min_s", "completed_wall_max_s", "completed_cpu_median_s"])
        for group in groups:
            wall, cpu, rss = (group[k] for k in ("wall_seconds", "cpu_seconds", "max_rss_kib"))
            writer.writerow([group["stop_ns"], group["profile"], group["accepted"], group["expected_repeats"],
                             *[wall[k] if wall else None for k in ("median", "min", "max")],
                             group["speedup"], cpu["median"] if cpu else None, group["cpu_cost_ratio"],
                             rss["median"] / 1024 if rss else None, group["completed"],
                             *[group["completed_wall_seconds"][k] if group["completed_wall_seconds"] else None
                               for k in ("median", "min", "max")],
                             group["completed_cpu_seconds"]["median"] if group["completed_cpu_seconds"] else None])
    charts = {}
    for name, field, title, unit in [
        ("wall", "wall_seconds", f'{config["tool"].upper()} end-to-end wall time', "s"),
        ("speedup", "speedup", "Speedup relative to the same-endpoint baseline", "×"),
        ("cpu", "cpu_seconds", "Container CPU cost (cgroup delta; waited-tree fallback)" if config["tool"] == "openlane"
         else "Aggregate CPU cost (user + system, all waited children)", "s"),
    ]:
        svg = chart(groups, config, field, title, unit)
        (output / f"{name}.svg").write_text(svg)
        charts[name] = svg
    lines = [f'# {config["tool"].upper()} · {config["case"]} 基准报告', "",
             f'实验状态：**{campaign["status"]}**。预热 {summary["warmups"]} 次；计时 {summary["measurements"]} 次。', "",
             "## 适用范围", config["baseline_scope"], "",
             "这是公共参考电路的实测，不是客户私有网表的性能认证。只比较同一终止时间下的配置，不把不同 TSTOP 的波形前缀视为必然相同。", "",
             "## 结论", *[f"- {item}" for item in findings], "",
             "## 结果", "中位数与实测最小–最大值；预热不计入；不删除离群点。加速比是同终止时间的基线中位数除以该配置中位数。", "",
             "† / 斜线柱：完整运行但该组尚未全部通过资格校验的诊断耗时，不生成合格加速比。合格/计划列显示数值与控制均通过的次数。差异超过本轮预设门槛，不等于已经证实电路结果错误。", "",
             "| 终止时间 | 配置 | 合格/计划 | Wall 秒，中位数 [最小–最大] | 加速比 | 总 CPU 秒 | CPU 成本比 | 峰值单进程 RSS MiB |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    table_rows = []
    for group in groups:
        prefix = "" if group["complete"] else "completed_"
        wall, cpu, rss = (group[prefix + k] for k in ("wall_seconds", "cpu_seconds", "max_rss_kib"))
        marker = " †" if not group["complete"] else ""
        cells = [f'{group["stop_ns"]:g} ns' if group["stop_ns"] is not None else "完整流程", group["profile"],
                 f'{group["accepted"]}/{group["expected_repeats"]}',
                 f'{wall["median"]:.2f} [{wall["min"]:.2f}–{wall["max"]:.2f}]{marker}' if wall else "—",
                 f'{group["speedup"]:.3f}×' if group["speedup"] is not None else "未认证",
                 f'{cpu["median"]:.2f}' if cpu else "—",
                 f'{group["cpu_cost_ratio"]:.2f}×' if group["cpu_cost_ratio"] is not None else "—",
                 f'{rss["median"] / 1024:.1f}' if rss else "—"]
        table_rows.append(cells)
        lines.append("| " + " | ".join(cells) + " |")
    methods = [
        "平台：" + ("；".join(platforms) or "未记录") + "。工具路径与 SHA256 见 summary.json 的 tool_identities；不混入其它内核/二进制的历史时间。",
        "Wall 包含控制启动、工具完整命令执行与退出；输入复制、运行前后快照、结果校验在计时区间外。原生工具记录 GNU time 的 wall 与进程树 user+system CPU。OpenLane CPU 优先使用容器 cgroup 增量，缺失时回退到 wait 进程树；计量器成本也在容器范围内。外层 wall 包含 Docker 启停，不把客户端 CPU 当作流程 CPU。",
        "先完成所有终止时间的基线预热，再进行调优预热；计时采用固定随机种子的完整区组，串行执行。无并发 EDA 任务；运行前作一秒 CPU 空闲检查，但这不是整机独占或屏蔽中断。",
        f'默认 CPU 池 {config["controls"]["cpus"]}；控制器 CPU{config["controls"]["controller_cpu"]}；内存节点 {config["controls"]["memory_node"]}。逐配置覆盖值见 campaign.json。原生进程使用 MPOL_BIND；容器使用 cgroup cpuset.cpus / cpuset.mems。逐 pthread 固定到一个 CPU，并用 /proc 抽样复核。',
        f'频率锁定：{"已记录锁定" if summary["frequency_locked"] else "未锁定"}。策略为 {config["controls"].get("frequency_policy", "observe")}；仅保存 /proc/cpuinfo 报告值不构成恒频证明。require-fixed 在无法认证时拒绝运行。未修改全机频率、Turbo、内核或隔离设置。',
        "共享/文件映射页不一定迁移到目标 NUMA 节点。/proc 审计有采样间隔；LD_PRELOAD 不能覆盖静态链接、私有 clone 或安全执行路径。结果不是对所有历史调度事件的形式化证明。",
        "原始公共输入不修改；每次运行复制并记录依赖闭包 SHA256。原生 SPICE 适配器重定位 include 并改变 TSTOP；其它显式选项见配置。Calibre 与 OpenLane 比较完整 DRC / RTL→GDS，不使用 ns 作为横轴。",
        "峰值 RSS 是 GNU time / wait 统计的最大单个进程驻留集，不是所有并行进程瞬时内存之和。重复测量的范围不是置信区间；速度结论只覆盖本机、本模型与本次受控条件。",
    ]
    if any(r.get("binding_audit") and "specific_assignment_verified" not in r["binding_audit"] for r in records):
        methods.append("本批使用冻结的早期控制版本，按宿主 PID/TID 对照指定 CPU 与抽样掩码；日志没有任务起始 tick，不能追补为新版的生命周期身份审计。执行源码归档与 SHA256 位于结果目录 execution-source.tar.gz；/proc 抽样也不是连续调度追踪。")
    if config["tool"] == "hspice":
        methods += [
            "HSPICE Wall 覆盖许可证检出、解析、工作点、完整瞬态、原有输出写盘与退出。不把 HSPICE 自报 CPU 当作聚合 CPU。完成条件包括正常退出、原有打印电压的完整栅格、有限值、实际最终时间和实际计算线程数；调优逐点对照同终止时间基线。比较不覆盖未输出内部状态或二进制全精度。",
            f'数值资格门槛预先固定为 abs {config.get("waveform_absolute_tolerance_v", 0.0001):g} V + rel {config.get("waveform_relative_tolerance", 0.0001):g} × |基线电压|。不在看到结果后放宽。详细差异与超限样本数见 runs.csv；这不是客户签核容差。',
            "默认 C5315 矩阵保留 123 路打印电压，只改 TSTOP 与 -mt，不改变激励、容差、积分法或打印步长。KMP_AFFINITY=disabled 防止厂商运行时覆盖绑核；mt8-passive 额外指定 OMP_WAIT_POLICY=PASSIVE。后者只是候选配置，不预设它被采用或会加速。",
        ]
    lines += ["", "## 方法与限制", *[f"- {item}" for item in methods], "", "## 原始证据", "",
              "每次运行保留 run.json、inputs.json、暂存网表、time.txt、console.log、test.lis、pin.jsonl、observations.jsonl。",
              "原始列表包含机器/许可证元数据，仅在私有结果目录保留。对外分享本报告、汇总 CSV 和图表即可，不默认公开完整 .lis。",
              "", f'失败运行：{", ".join(summary["failed_runs"]) or "无"}。', "",
              "可机器读取：summary.json、summary.csv、runs.csv。冻结执行代码的 SHA256 记录在 campaign.json。"]
    (output / "REPORT.zh-CN.md").write_text("\n".join(lines) + "\n")
    headers = ["终止时间", "配置", "合格/计划", "Wall 秒，中位数 [min–max]", "加速比", "总 CPU 秒", "CPU 成本比", "RSS MiB"]
    table = '<table><thead><tr>' + ''.join(f'<th>{escape(c)}</th>' for c in headers) + '</tr></thead><tbody>'
    table += ''.join('<tr>' + ''.join(f'<td>{escape(c)}</td>' for c in row) + '</tr>' for row in table_rows) + '</tbody></table>'
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(config['tool'].upper())} 基准报告</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f8fafc;color:#0f172a;font:16px/1.7 system-ui,sans-serif}}main{{max-width:1360px;margin:auto;padding:40px 28px}}h1{{font-size:34px;line-height:1.2}}h2{{margin-top:38px}}.note{{padding:18px 22px;border-left:4px solid #d97706;background:#fffbeb}}.chart{{margin:24px 0;background:white;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden}}svg{{display:block;width:100%;height:auto}}.table{{overflow:auto}}table{{border-collapse:collapse;min-width:1060px;width:100%;font-size:14px}}th,td{{text-align:right;white-space:nowrap;padding:10px 12px;border-bottom:1px solid #e2e8f0}}th{{background:#e2e8f0}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}li{{margin-bottom:12px}}footer{{color:#64748b;margin-top:36px}}@media(max-width:600px){{main{{padding:22px 14px}}h1{{font-size:27px}}}}@media print{{main{{padding:0}}.chart{{break-inside:avoid}}}}
</style><main><p>{escape(summary['generated_utc'])} · 可复现 EDA 基准</p><h1>{escape(config['tool'].upper())} / {escape(config['case'])}</h1>
<p>状态：<strong>{escape(campaign['status'])}</strong> · 预热 {summary['warmups']} 次 · 计时 {summary['measurements']} 次</p>
<div class="note"><strong>结论边界：</strong>{escape(config['baseline_scope'])}<br>公共参考电路，非客户网表等价物。CPU / NUMA 控制详见逐次记录；频率锁定：{escape(str(summary['frequency_locked']))}。</div>
<h2>结论</h2><ul>{''.join('<li>' + escape(item) + '</li>' for item in findings)}</ul>
<h2>端到端耗时</h2><div class="chart">{charts['wall']}</div><h2>加速比</h2><div class="chart">{charts['speedup']}</div>
<h2>CPU 成本</h2><div class="chart">{charts['cpu']}</div><h2>全部计时结果</h2><p>预热不参与统计；保留所有计时样本。范围为实测 min–max，不是置信区间。† / 斜线柱是完整运行但未全部通过资格校验的诊断耗时，不生成合格加速比。差异超过本轮门槛，不等于证实电路结果错误。</p><div class="table">{table}</div>
<h2>方法与限制</h2><ul>{''.join('<li>' + escape(m) + '</li>' for m in methods)}</ul>
<h2>证据与复现</h2><p>原始结果以 campaign.json 和每次运行的 run.json 为准。summary.json、summary.csv、runs.csv 可用于再分析。失败运行：{escape(', '.join(summary['failed_runs']) or '无')}。</p>
<p>完整 .lis 可能包含许可证/用户元数据，不随本报告公开。冻结执行代码的 SHA256 已记录。</p><footer>Generated {escape(summary['generated_utc'])} · 自包含报告，无外部脚本、字体或网络依赖。</footer></main></html>'''
    (output / "report.html").write_text(html)
    json_write(output / "SHA256SUMS.json", {p.name: sha256(p) for p in sorted(output.iterdir())
                                           if p.is_file() and p.name != "SHA256SUMS.json"})
    return summary
