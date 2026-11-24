#!/usr/bin/env python3
"""Read-only audit and extraction for the preserved 20 ns allocator batch."""
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
DATA = PACKAGE / "result/dynamic_library/20ns/measurements"
FORMAL = DATA / "formal-20ns/formal"
OUT = PACKAGE / "docs/analysis"
CONFIGS = "ABCD"
PHASE_KEYS = [
    "Total elapsed time (seconds)", "Netlist loading time",
    "Subckt and Param expansion time", "Netlist parsing time",
    "Total analysis time (seconds)", "Matrix load time",
    "Matrix synchronize time", "Matrix reorder time", "Matrix factor time",
    "Matrix solve time", "Transient analysis time", "Transient load time",
    "Transient sync time", "Transient factor time", "Transient solve time",
    "Transient trunc time", "AC analysis time", "AC load time",
    "AC sync time", "AC factor time", "AC solve time",
]


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_dirs():
    for phase, base in (("warmup", DATA / "formal-20ns/warmup"),
                        ("formal", FORMAL)):
        for config in CONFIGS:
            folder = base / config
            if phase == "formal":
                for block in sorted(base.glob("block-*")):
                    run = block / config
                    if run.is_dir():
                        yield phase, config, block.name, run
            elif folder.is_dir():
                yield phase, config, "warmup", folder


def formal_dirs():
    for block in sorted(FORMAL.glob("block-*")):
        for config in CONFIGS:
            run = block / config
            if run.is_dir():
                yield block.name, config, run


def write_csv(path: Path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        safe_rows = []
        for row in rows:
            safe_rows.append({key: (value.encode("utf-8", "replace").decode("utf-8")
                                    if isinstance(value, str) else value)
                              for key, value in row.items()})
        writer.writerows(safe_rows)


def extract_runs():
    rows = []
    for block, config, folder in formal_dirs():
        metrics = read(folder / "metrics.json")
        execution = read(folder / "execution.json")
        validation = read(folder / "validation.json")
        rows.append({
            "batch": "20ns-formal",
            "phase": "formal",
            "config": config,
            "block": block,
            "execution_order": "N/A",
            "wall_s": metrics.get("wall", "N/A"),
            "user_cpu_s": metrics.get("user", "N/A"),
            "system_cpu_s": metrics.get("system", "N/A"),
            "max_rss_kib": metrics.get("rss_kib", "N/A"),
            "minor_faults": metrics.get("minor_faults", "N/A"),
            "major_faults": metrics.get("major_faults", "N/A"),
            "voluntary_cs": metrics.get("voluntary_cs", "N/A"),
            "involuntary_cs": metrics.get("involuntary_cs", "N/A"),
            "cpu_affinity": "0-15",
            "monitor_cpu": 16,
            "numa_policy": "default",
            "threads": execution.get("profile", {}).get("threads", "N/A"),
            "solver": execution.get("profile", {}).get("solver", "N/A"),
            "ld_preload": execution.get("ld_preload", "N/A"),
            "elf": execution.get("elf", "N/A"),
            "elf_sha256": execution.get("sha256", "N/A"),
            "waveform_passed": validation.get("passed", "N/A"),
            "metrics_path": str(folder / "metrics.json"),
            "execution_path": str(folder / "execution.json"),
            "validation_path": str(folder / "validation.json"),
            "maps_path": str(folder / "maps.txt"),
        })
    return rows


def extract_phases():
    rows = []
    for block, config, folder in formal_dirs():
        phases = read(folder / "metrics.json").get("phases", {})
        for phase in PHASE_KEYS:
            rows.append({"batch": "20ns-formal", "config": config,
                         "block": block, "phase": phase,
                         "seconds": phases.get(phase, "N/A"),
                         "source": str(folder / "metrics.json")})
    return rows


def inventory():
    rows = []
    candidates = [DATA / "design.json", DATA / "selection.json",
                  DATA / "formal-20ns/summary.json",
                  DATA / "log/formal-20ns.log"]
    for path in candidates:
        if path.is_file():
            rows.append({"category": "batch", "config": "ABCD",
                         "block": "N/A", "run": "N/A", "file": str(path),
                         "size_bytes": path.stat().st_size, "sha256": digest(path)})
    for block, config, folder in formal_dirs():
        for name, category in (("metrics.json", "metrics"),
                               ("execution.json", "execution"),
                               ("validation.json", "validation"),
                               ("maps.txt", "maps"),
                               ("console.log", "console"),
                               ("time.tsv", "timing"),
                               ("waveform.raw", "waveform")):
            path = folder / name
            rows.append({"category": category, "config": config,
                         "block": block, "run": f"{block}/{config}",
                         "file": str(path),
                         "size_bytes": path.stat().st_size if path.exists() else "N/A",
                         "sha256": digest(path) if path.exists() else "N/A"})
    return rows


def paired(rows):
    values = {c: {} for c in CONFIGS}
    for row in rows:
        values[row["config"]][row["block"]] = float(row["wall_s"])
    result = []
    for left, right in (("A", "B"), ("A", "C"), ("C", "D"), ("A", "D")):
        diffs = [values[left][b] - values[right][b] for b in sorted(values[left])]
        mean = statistics.mean(diffs)
        sd = statistics.stdev(diffs)
        ci = 2.446912 * sd / math.sqrt(len(diffs))
        result.append({"comparison": f"{left}->{right}",
                       "n": len(diffs), "mean_saving_s": mean,
                       "ci95_low_s": mean - ci, "ci95_high_s": mean + ci,
                       "faster_pairs": sum(x > 0 for x in diffs),
                       "improvement_percent": mean / statistics.mean(values[left].values()) * 100})
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = extract_runs()
    run_header = list(rows[0])
    write_csv(OUT / "run-inventory.csv", run_header, rows)
    write_csv(OUT / "phase-timing.csv",
              ["batch", "config", "block", "phase", "seconds", "source"],
              extract_phases())
    write_csv(OUT / "data-inventory.csv",
              ["category", "config", "block", "run", "file", "size_bytes", "sha256"],
              inventory())
    write_csv(OUT / "performance-summary.csv",
              ["comparison", "n", "mean_saving_s", "ci95_low_s",
               "ci95_high_s", "faster_pairs", "improvement_percent"],
              paired(rows))
    phase_rows = extract_phases()
    aggregates = []
    for config in CONFIGS:
        for phase in PHASE_KEYS:
            vals = [float(x["seconds"]) for x in phase_rows
                    if x["config"] == config and x["phase"] == phase]
            aggregates.append({"config": config, "phase": phase, "n": len(vals),
                               "mean_s": statistics.mean(vals) if vals else "N/A",
                               "sd_s": statistics.stdev(vals) if len(vals) > 1 else "N/A"})
    write_csv(OUT / "phase-timing-summary.csv",
              ["config", "phase", "n", "mean_s", "sd_s"], aggregates)
    print(f"formal_runs={len(rows)}")
    print(f"waveform_pass={sum(r['waveform_passed'] is True for r in rows)}/{len(rows)}")
    print(f"paired_comparisons={len(paired(rows))}")


if __name__ == "__main__":
    main()
