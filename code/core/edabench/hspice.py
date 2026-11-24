"""HSPICE adapter: customer-style CLI, native printed waveforms, no ngspice flags."""

from __future__ import annotations

import math
from pathlib import Path
import re

from .inputs import sha256, stage_spice

TOOL = "/opt/eda_public/bin/hspice"
BINARY = "/opt/vendor/synopsys/hspice/P-2019.06-2/hspice/linux64/hspice"
PROCESS_NAMES = {"hspice"}
DEFAULT_SOURCE = ("/opt/eda_public/benchmarks/spice/ngspice-examples-a80f6e3/"
                  "examples/klu/Circuits/85/c5315/c5315_ann.net")


def prepare(run: Path, config: dict, profile: dict, stop_ns: float) -> dict:
    identity = stage_spice(Path(config.get("source", DEFAULT_SOURCE)), run, stop_ns)
    extra = profile.get("options", [])
    if extra:
        deck = run / "circuit.sp"
        text = deck.read_bytes().decode("latin1")
        text = re.sub(r"^\s*\.end\s*$", "\n".join(extra) + "\n.end", text,
                      count=1, flags=re.I | re.M)
        deck.write_bytes(text.encode("latin1"))
    return {"command": [TOOL, "-i", "circuit.sp", "-o", "test",
                        *profile.get("cli", [])], "inputs": identity,
            "initialization_files": [
                {"path": str(path), "sha256": sha256(path) if path.is_file() else None}
                for path in (run / "hspice.ini", Path.home() / "hspice.ini",
                             Path(BINARY).parent.parent / "hspice.ini")],
            "output_contract": "All original .print voltages, original print step and precision"}


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?([a-zA-Z]*)$")
_SCALE = {"": 1.0, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
          "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}


def number(token: str) -> float:
    match = _NUMBER.fullmatch(token)
    if not match or match[1].lower() not in _SCALE:
        raise ValueError(token)
    suffix = match[1]
    numeric = token[:-len(suffix)] if suffix else token
    return float(numeric.replace("D", "e").replace("d", "e")) * _SCALE[suffix.lower()]


def read_waveforms(path: Path) -> dict[str, list[tuple[float, float]]]:
    """Parse HSPICE .print blocks, including page/group boundaries."""
    lines = path.read_bytes().decode("latin1").splitlines()
    signals: dict[str, dict[float, float]] = {}
    columns: list[str] = []
    for index, line in enumerate(lines):
        tokens = line.split()
        if tokens and tokens[0].lower() == "time" and len(tokens) > 1:
            names = lines[index + 1].split() if index + 1 < len(lines) else []
            if len(names) == len(tokens) - 1:
                columns = names
            continue
        if not columns or len(tokens) != len(columns) + 1:
            continue
        try:
            values = [number(token) for token in tokens]
        except ValueError:
            continue
        for signal, value in zip(columns, values[1:]):
            time = values[0]
            previous = signals.setdefault(signal, {}).get(time)
            if previous is not None and previous != value:
                raise ValueError(f"Conflicting printed waveform at {signal}/{time}")
            signals[signal][time] = value
    return {signal: sorted(values.items()) for signal, values in signals.items()}


def validate(run: Path, config: dict, stop_ns: float, reference: Path | None = None) -> dict:
    listing = run / "test.lis"
    if not listing.is_file():
        return {"passed": False, "reason": "missing HSPICE listing"}
    text = listing.read_bytes().decode("latin1")
    waveforms = read_waveforms(listing)
    deck = (run / "circuit.sp").read_text()
    wanted = re.findall(r"\bv\(([^)]+)\)", " ".join(
        line for line in deck.splitlines()
        if re.match(r"\s*\.print\s+tran\b", line, re.I)), re.I)
    tran = re.search(r"^\s*\.tran\s+(\S+)", deck, re.I | re.M)
    step = number(tran[1].lower().removesuffix("s")) if tran else None
    expected_end = stop_ns * 1e-9
    expected_points = round(expected_end / step) + 1 if step else None
    finite = all(math.isfinite(t) and math.isfinite(v)
                 for points in waveforms.values() for t, v in points)
    complete = bool(waveforms) and all(
        len(points) == expected_points and len(points) > 1
        and abs(points[-1][0] - expected_end) <= max(1e-18, expected_end * 1e-5)
        and all(abs(t - i * step) <= max(1e-18, step * 1e-4)
                for i, (t, _) in enumerate(points)) for points in waveforms.values())
    coverage = set(map(str.lower, waveforms)) == set(map(str.lower, wanted))
    errors = len(re.findall(r"^\s*\*\*(?:error|fatal)\*\*", text, re.I | re.M))
    result = {"passed": "job concluded" in text.lower() and finite and complete and coverage and errors == 0,
              "fatal_or_error_messages": errors,
              "job_concluded": "job concluded" in text.lower(), "finite": finite,
              "reached_stop": complete, "expected_print_signals": len(wanted),
              "expected_points_per_signal": expected_points,
              "parsed_signals": len(waveforms),
              "samples_per_signal": sorted(set(map(len, waveforms.values()))),
              "first_time": min((p[0][0] for p in waveforms.values()), default=None),
              "last_time": max((p[-1][0] for p in waveforms.values()), default=None),
              "comparison_scope": "all original printed voltages; not unprinted internal state"}
    if reference:
        baseline = read_waveforms(reference / "test.lis")
        same_grid = baseline.keys() == waveforms.keys() and all(
            [t for t, _ in baseline[name]] == [t for t, _ in waveforms[name]] for name in baseline)
        max_difference = None
        outside_tolerance = 0
        identical = False
        absolute = config.get("waveform_absolute_tolerance_v", 0.0001)
        relative = config.get("waveform_relative_tolerance", 0.0001)
        if same_grid:
            max_difference = max(abs(a[1] - b[1]) for name in baseline
                                 for a, b in zip(baseline[name], waveforms[name]))
            outside_tolerance = sum(
                abs(a[1] - b[1]) > absolute + relative * abs(a[1])
                for name in baseline for a, b in zip(baseline[name], waveforms[name]))
            identical = max_difference == 0.0
        # Acceptance limits are fixed in the configuration before a tuning run.
        result["reference"] = str(reference)
        result["comparison"] = {"identical_time_grid": same_grid,
                                "maximum_absolute_printed_difference": max_difference,
                                "identical_printed_values": identical,
                                "absolute_tolerance_v": absolute,
                                "relative_tolerance": relative,
                                "samples_outside_tolerance": outside_tolerance,
                                "passed": same_grid and outside_tolerance == 0}
        result["passed"] &= result["comparison"]["passed"]
    stats = {}
    for label, pattern in {
        "actual_threads": r"Actual Threads Count\s*:\s*(\d+)",
        "command_line_threads": r"Command Line Threads Count\s*:\s*(\d+)",
        "reported_total_cpu_seconds": r"total cpu time\s*[:=]?\s*([\d.]+)",
        "reported_elapsed_seconds": r"total elapsed time\s*[:=]?\s*([\d.]+)",
        "license_checkout_seconds": r"total license checkout elapse time:\s*([\d.]+)",
        "nodes": r"# nodes\s*=\s*(\d+)",
        "elements": r"# elements\s*=\s*(\d+)",
    }.items():
        found = re.search(pattern, text, re.I)
        if found:
            stats[label] = float(found[1])
    for stage in ("op point", "transient", "readin", "errchk", "setup", "output"):
        found = re.search(r"^\s*" + stage + r"\s+([\d.]+)(?:\s+(\d+)\s+(\d+))?", text, re.M)
        if found:
            stats[stage.replace(" ", "_") + "_reported_seconds"] = float(found[1])
            if found[2]:
                stats[stage.replace(" ", "_") + "_points"] = int(found[2])
                stats[stage.replace(" ", "_") + "_iterations"] = int(found[3])
    result["simulator_statistics"] = stats
    result["warnings"] = len(re.findall(r"\*\*warning\*\*", text, re.I))
    return result
