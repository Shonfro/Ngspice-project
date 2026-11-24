"""Native ngspice: controlled, headless C7552/SKY130.

The SKY130 source's .control must never reach ``source``: it runs a 15ns
analysis immediately. Only the supported original control sequence is moved
to our driver. Circuit/model/stimulus options are not performance knobs here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shlex

from .inputs import json_write, sha256, spice_closure, stage_spice

TOOL = "/opt/eda_public/bin/ngspice"
BINARY = "/opt/vendor/ngspice/current/bin/ngspice"
PROCESS_NAMES = {"ngspice"}
_INCLUDE = re.compile(r"^[ \t]*\.(include|inc|lib)[ \t]+([^\r\n]+)", re.I | re.M)
_CONTROL = re.compile(r"^[ \t]*\.(control|endc)\b", re.I | re.M)
_VOLTAGE = re.compile(r"v\([a-z0-9_.$:!+-]+\)", re.I)


def _voltages(text: str) -> list[str]:
    signals = text.lower().split()
    if not signals or any(not _VOLTAGE.fullmatch(signal) for signal in signals):
        raise ValueError("Only explicit single-node voltage signals are supported")
    if len(signals) != len(set(signals)):
        raise ValueError("Duplicate voltage signals")
    return signals


def _check_dependencies(texts: dict[Path, str]) -> None:
    # A sourced include can also contain commands; never hide a second analysis.
    for path, text in texts.items():
        if _CONTROL.search(text) or re.search(
                r"^[ \t]*\.(tran|ac|dc|op|pz|tf|noise|disto|sens)\b", text, re.I | re.M):
            raise ValueError(f"Unsupported control/analysis in dependency: {path}")


def _stage_sky130(run: Path, config: dict, stop_ns: float) -> tuple[dict, list[str], list[str], str]:
    source = Path(config["source"]).resolve(strict=True)
    original = source.read_bytes()
    text = original.decode("latin1")
    controls = list(_CONTROL.finditer(text))
    if [match[1].lower() for match in controls] != ["control", "endc"]:
        raise ValueError("SKY130 requires exactly one original .control/.endc block")
    block = re.search(r"^[ \t]*\.control[ \t]*\r?\n(.*?)^[ \t]*\.endc[ \t]*(?:\r?\n|$)",
                      text, re.I | re.M | re.S)
    if not block:
        raise ValueError("Unsupported .control block delimiters")
    commands = [line.strip() for line in block[1].splitlines()
                if line.strip() and not line.lstrip().startswith("*")]
    normalized = [" ".join(line.lower().split()) for line in commands]
    if (len(commands) != 8 or normalized[0] != "unset ngdebug"
            or not normalized[1].startswith("save ") or normalized[2] != "option noinit"
            or normalized[4:] != ["set nolegend", "plot allv", "rusage all", "inventory"]):
        raise ValueError("Unsupported original SKY130 control sequence")
    original_signals = _voltages(commands[1].split(None, 1)[1])
    signals = _voltages(" ".join(config["saved_signals"]))
    if not set(signals) <= set(original_signals):
        raise ValueError("Selected signals must be in the original save list")
    tran = re.fullmatch(r"(tran[ \t]+\S+[ \t]+)(\S+)([ \t]+\S+[ \t]+\S+[ \t]+uic)",
                        commands[3], re.I)
    if not tran:
        raise ValueError("Expected original tran TSTEP TSTOP TSTART TMAX uic; UIC is never added")
    analysis = tran[1] + f"{stop_ns:g}ns" + tran[3]
    body = text[:block.start()] + text[block.end():]
    if re.search(r"^[ \t]*\.(tran|ac|dc|op|pz|tf|noise|disto|sens|save|print|plot)\b",
                 body, re.I | re.M):
        raise ValueError("Unsupported analysis/output outside the original control block")
    if len(re.findall(r"^[ \t]*\.end[ \t]*$", body, re.I | re.M)) != 1:
        raise ValueError("Expected one final .end")

    library = Path(config["pdk_library"]).resolve(strict=True)
    replaced = []

    def replace_library(match: re.Match) -> str:
        if match[1].lower() != "lib":
            return match[0]
        # The original Windows literal must not be parsed with POSIX escaping.
        tokens = shlex.split(match[2], posix=False)
        if (len(tokens) != 2 or tokens[1].lower() != "tt"
                or re.split(r"[\\/]", tokens[0].strip("\"'"))[-1].lower() != "sky130.lib.spice"):
            raise ValueError("Only the original literal SKY130 .lib <file> tt is supported")
        replaced.append(match[2])
        return f'.lib "{library}" {tokens[1]}'

    body = _INCLUDE.sub(replace_library, body)
    if len(replaced) != 1:
        raise ValueError("Expected exactly one original SKY130 library reference")
    dependencies = {}
    for match in _INCLUDE.finditer(body):
        tokens = shlex.split(match[2])
        if not tokens or (match[1].lower() == "lib" and len(tokens) != 2):
            raise ValueError("Unsupported top-level include/library directive")
        dependencies.update(spice_closure((source.parent / tokens[0]).resolve(strict=True)))
    _check_dependencies(dependencies)
    if source in dependencies:
        raise ValueError("Source may not be included by its dependencies")
    hashes = {path: sha256(path) for path in dependencies}
    names = {path: f"{hashes[path][:16]}-{path.name}" for path in dependencies}
    (run / "models").mkdir()
    for path, content in {source: body, **dependencies}.items():
        def include(match: re.Match) -> str:
            tokens = shlex.split(match[2])
            if match[1].lower() == "lib" and len(tokens) == 1:
                return match[0]  # A library section, not an external file.
            child = (path.parent / tokens[0]).resolve(strict=True)
            prefix = "models/" if path == source else ""
            suffix = " " + " ".join(tokens[1:]) if len(tokens) > 1 else ""
            return f'.{match[1]} "{prefix}{names[child]}"{suffix}'

        adapted = _INCLUDE.sub(include, content)
        target = run / "circuit.sp" if path == source else run / "models" / names[path]
        target.write_bytes(adapted.encode("latin1"))
    (run / "original.sp").write_bytes(original)
    identity = {
        "source": str(source),
        "closure": [{"path": str(path), "sha256": sha256(source) if path == source else hashes[path],
                     "staged": "circuit.sp" if path == source else f"models/{names[path]}"}
                    for path in sorted([source, *dependencies])],
        "original_copy": "original.sp",
        "transformations": ["resolve literal include paths",
                            {"directive": "pdk_library", "original": replaced[0], "new": str(library)},
                            {"directive": "tran_stop", "original": tran[2], "new": f"{stop_ns:g}ns"},
                            "move supported original control into driver; ngbehavior=hsa before source",
                            "headless output instrumentation: selected saved voltages, no GUI plot-all"],
        "saved_signals": signals,
        "original_saved_signals": original_signals,
        "circuit_model_stimulus_tolerances_unchanged": True,
    }
    json_write(run / "inputs.json", identity)
    return identity, signals, [commands[0], commands[2]], analysis


def prepare(run: Path, config: dict, profile: dict, stop_ns: float) -> dict:
    if not math.isfinite(stop_ns) or stop_ns <= 0:
        raise ValueError("A finite positive stop_ns is required")
    threads = profile["threads"]
    if type(threads) is not int or threads < 1:
        raise ValueError("ngspice threads must be a positive integer")
    solver = profile.get("solver", "klu")
    if solver not in {"klu", "sparse"}:
        raise ValueError("ngspice solver must be klu or sparse")
    for key, value in {**config.get("environment", {}), **profile.get("environment", {})}.items():
        if key in {"OMP_NUM_THREADS", "OMP_THREAD_LIMIT"} and str(value) != str(threads):
            raise ValueError(f"{key} must match the configured thread count")
    sky130 = "pdk_library" in config
    if sky130:
        identity, signals, original_options, analysis = _stage_sky130(run, config, stop_ns)
        output = ("Headless selected-output instrumentation: " + ", ".join(signals)
                  + "; NOT original plot-all IO. Original adaptive tran TSTEP/TSTART/TMAX/UIC preserved.")
    else:
        source = Path(config["source"]).resolve(strict=True)
        if _CONTROL.search(source.read_bytes().decode("latin1")):
            raise ValueError("Embedded .control requires the supported SKY130 path")
        texts = spice_closure(source)
        _check_dependencies({path: text for path, text in texts.items() if path != source})
        identity = stage_spice(source, run, stop_ns)
        deck = (run / "circuit.sp").read_bytes().decode("latin1")
        signals = _voltages(" ".join(
            re.sub(r"^[ \t]*\.print[ \t]+tran[ \t]+", "", line, flags=re.I)
            for line in deck.splitlines() if re.match(r"\s*\.print\s+tran\b", line, re.I)))
        original_options, analysis = [], "run"
        output = "All original printed voltages also saved in native ASCII raw"
    # These are output-only settings, not numerical solver/timestep changes.
    (run / "driver.cir").write_text(
        "* EDA benchmark ngspice headless driver\n* " + output + "\n.control\n" +
        ("set ngbehavior=hsa\n" if sky130 else "") +
        f"set num_threads={threads}\n"
        "* Headless output instrumentation: full-precision ASCII saved waveforms\n"
        "set filetype=ascii\nset rawfileprec=17\n"
        "source circuit.sp\n" + ("option klu\n" if solver == "klu" else "option klu=0\n") +
        "".join(option + "\n" for option in original_options) +
        "save " + " ".join(signals) + "\n" + analysis + "\n"
        "echo BENCH_NUM_THREADS=$num_threads\n"
        "echo BENCH_OMP_NUM_THREADS=$OMP_NUM_THREADS\n"
        "write waveform.raw " + " ".join(signals) + "\nrusage all\n" +
        ("inventory\n" if sky130 else "") +
        "echo BENCH_SIMULATION_COMPLETE\nquit\n.endc\n.end\n")
    json_write(run / "ngspice-contract.json", {"signals": signals, "solver": solver,
               "threads": threads, "stop_ns": stop_ns, "output_contract": output})
    return {"command": [TOOL, "-n", "-b", "driver.cir"], "inputs": identity,
            "output_contract": output}


def read_raw(path: Path) -> tuple[list[str], list[list[float]]]:
    """Read one real transient plot; tolerate ngspice's blank sample separators."""
    with path.open() as stream:
        names = []
        count = None
        points = None
        flags = None
        plot = None
        values = False
        for line in stream:
            if line.startswith("No. Variables:"):
                count = int(line.split(":", 1)[1])
            elif line.startswith("No. Points:"):
                points = int(line.split(":", 1)[1])
            elif line.startswith("Flags:"):
                flags = line.split(":", 1)[1].split()
            elif line.startswith("Plotname:"):
                plot = line.split(":", 1)[1].strip().lower()
            elif line.strip() == "Variables:":
                if count is None or count < 2:
                    raise ValueError("Missing raw variable count")
                for index in range(count):
                    fields = stream.readline().split()
                    if len(fields) < 3 or fields[0] != str(index):
                        raise ValueError("Invalid raw variable table")
                    names.append(fields[1].lower())
            elif line.strip() == "Values:":
                values = True
                break
        if (not names or points is None or points < 1 or not values
                or flags != ["real"] or plot != "transient analysis"):
            raise ValueError("Incomplete or unsupported real transient ASCII raw header")

        def data_line() -> list[str]:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("Truncated ASCII raw value line")
                if line.strip():
                    return line.split()
            raise ValueError("Truncated ASCII raw samples")

        rows = []
        for index in range(points):
            first = data_line()
            if len(first) != 2 or first[0] != str(index):
                raise ValueError("Unexpected raw sample index")
            row = [float(first[1])]
            for _ in names[1:]:
                fields = data_line()
                if len(fields) != 1:
                    raise ValueError("Unexpected raw value")
                row.append(float(fields[0]))
            rows.append(row)
        if any(line.strip() for line in stream):
            raise ValueError("Unexpected trailing raw data/plots")
    return names, rows


def _waveform_checks(names: list[str], rows: list[list[float]], signals: list[str], stop_ns: float) -> dict:
    finite = all(math.isfinite(value) for row in rows for value in row)
    end = stop_ns * 1e-9
    reached = bool(rows) and math.isclose(rows[-1][0], end, rel_tol=1e-12, abs_tol=1e-21)
    monotonic = (len(rows) > 1 and all(0 <= row[0] <= end + max(1e-21, end * 1e-12) for row in rows)
                 and all(a[0] < b[0] for a, b in zip(rows, rows[1:])))
    coverage = (names[0:1] == ["time"] and len(names) == len(set(names))
                and set(names[1:]) == set(signals))
    return {"passed": finite and reached and monotonic and coverage,
            "finite": finite, "reached_stop": reached, "monotonic_times": monotonic,
            "signal_coverage": coverage, "signals": names, "expected_signals": signals,
            "points": len(rows), "first_time": rows[0][0] if rows else None,
            "last_time": rows[-1][0] if rows else None}


def validate(run: Path, config: dict, stop_ns: float, reference: Path | None = None) -> dict:
    try:
        contract = json.loads((run / "ngspice-contract.json").read_text())
        if not math.isfinite(stop_ns) or stop_ns <= 0 or contract["stop_ns"] != stop_ns:
            raise ValueError("Requested endpoint differs from the prepared endpoint")
        names, rows = read_raw(run / "waveform.raw")
        console = (run / "console.log").read_text(errors="replace")
    except (OSError, ValueError, KeyError) as error:
        return {"passed": False, "reason": f"no complete saved waveform/contract: {error}"}
    result = _waveform_checks(names, rows, contract["signals"], stop_ns)
    klu = bool(re.search(r"^\s*Using KLU as Direct Linear Solver\s*$", console, re.M))
    sparse = bool(re.search(r"^\s*Using SPARSE 1\.3 as Direct Linear Solver\s*$", console, re.M))
    solver_match = (klu and not sparse) if contract["solver"] == "klu" else (sparse and not klu)
    counts = re.findall(r"^BENCH_NUM_THREADS=(\d+)\s*$", console, re.M)
    omp = re.findall(r"^BENCH_OMP_NUM_THREADS=(\d+)\s*$", console, re.M)
    threads_match = counts == [str(contract["threads"])] and omp == [str(contract["threads"])]
    complete = bool(re.search(r"^BENCH_SIMULATION_COMPLETE\s*$", console, re.M))
    result.update({"simulation_complete": complete, "klu_observed": klu,
                   "solver_observed_matches": solver_match, "configured_threads_match": threads_match,
                   "simulator_statistics": {"configured_threads": int(counts[0]) if len(counts) == 1 else None,
                                            "omp_num_threads": int(omp[0]) if len(omp) == 1 else None},
                   "thread_evidence_scope": "Simulator num_threads and environment readback, NOT active worker count",
                   "comparison_scope": contract["output_contract"]})
    result["passed"] &= complete and solver_match and threads_match
    if reference:
        try:
            ref_names, ref_rows = read_raw(reference / "waveform.raw")
            ref_result = validate(reference, config, stop_ns)
            same_inputs = json.loads((run / "inputs.json").read_text()) == json.loads(
                (reference / "inputs.json").read_text())
        except (OSError, ValueError) as error:
            result["passed"] = False
            result["comparison"] = {"passed": False, "reason": f"Invalid reference: {error}"}
            return result
        same_grid = names == ref_names and len(rows) == len(ref_rows) and all(
            a[0] == b[0] for a, b in zip(ref_rows, rows))
        maximum = max((abs(a - b) for x, y in zip(ref_rows, rows)
                       for a, b in zip(x[1:], y[1:])), default=0.0) if same_grid else None
        result["comparison"] = {"identical_time_grid": same_grid,
                                "reference_valid_at_same_endpoint": ref_result["passed"],
                                "same_inputs": same_inputs,
                                "maximum_absolute_difference": maximum,
                                "passed": ref_result["passed"] and same_inputs and same_grid and maximum == 0.0}
        result["passed"] &= result["comparison"]["passed"]
    return result
