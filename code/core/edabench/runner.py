"""One serial experiment lifecycle, shared by four tool-specific adapters."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import fcntl
import importlib
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time

from . import control
from .container_meter import audit_assignments
from .inputs import json_write, sha256

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("hspice", "calibre", "ngspice", "openlane")


def adapter(tool: str):
    if tool not in TOOLS:
        raise ValueError(f"Unknown tool: {tool}")
    return importlib.import_module(f"edabench.{tool}")


@contextlib.contextmanager
def campaign_lock():
    path = Path(f"/var/tmp/eda-benchmark-{os.getuid()}.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.write(str(os.getpid()) + "\n")
        stream.flush()
        yield


def load_config(path: Path, tool: str) -> dict:
    config = json.loads(path.read_text())
    if config.get("schema_version") != 1 or config.get("tool") != tool:
        raise ValueError("Configuration schema/tool mismatch")
    profiles = config["profiles"]
    if len({p["name"] for p in profiles}) != len(profiles):
        raise ValueError("Profile names must be unique")
    if sum(p["role"] == "baseline" for p in profiles) != 1:
        raise ValueError("Exactly one baseline profile is required")
    if (type(config.get("repeats")) is not int or config["repeats"] < 1
            or type(config.get("warmups")) is not int or config["warmups"] < 1
            or not math.isfinite(config.get("timeout_seconds", 0)) or config["timeout_seconds"] <= 0):
        raise ValueError("Positive integer repetitions/warmups and finite positive timeout required")
    stops = config.get("stop_times_ns")
    if tool in {"hspice", "ngspice"}:
        if not stops or any(not math.isfinite(stop) or stop <= 0 for stop in stops) or len(stops) != len(set(stops)):
            raise ValueError("SPICE endpoints must be unique finite positive nanoseconds")
    elif stops is not None:
        raise ValueError("Calibre/OpenLane run complete flows, not nanosecond endpoints")
    cpus = config["controls"]["cpus"]
    if not cpus or len(cpus) != len(set(cpus)):
        raise ValueError("CPU pool must be nonempty and unique")
    if config["controls"]["controller_cpu"] in cpus:
        raise ValueError("Controller must be outside the workload CPU pool")
    for profile in profiles:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile["name"]):
            raise ValueError("Profile names must be safe directory components")
        if profile["role"] not in {"baseline", "tuning"} or type(profile.get("threads")) is not int or profile["threads"] < 1:
            raise ValueError("Profile role and positive integer threads required")
        effective = {**config["controls"], **profile.get("controls", {})}
        if effective.get("frequency_policy", "observe") not in {"observe", "require-fixed"}:
            raise ValueError("Frequency policy must be observe or require-fixed")
        if not effective["cpus"] or len(effective["cpus"]) != len(set(effective["cpus"])):
            raise ValueError("Profile CPU pool must be nonempty and unique")
        if effective["controller_cpu"] in effective["cpus"]:
            raise ValueError("Profile controller must be outside the workload CPU pool")
        if profile.get("threads", 1) > len(effective["cpus"]):
            raise ValueError("Thread request exceeds the allocated CPU pool")
        environment = {**config.get("environment", {}), **profile.get("environment", {})}
        if any(key in {"LD_PRELOAD", "PYTHONPATH"} or key.startswith("EDABENCH_") for key in environment):
            raise ValueError("Experiment environment cannot override benchmark control machinery")
    return config


def cpu_ticks(cpus: list[int]) -> dict[int, tuple[int, int]]:
    answer = {}
    for line in Path("/proc/stat").read_text().splitlines():
        tokens = line.split()
        if tokens and tokens[0].startswith("cpu") and tokens[0][3:].isdigit():
            cpu = int(tokens[0][3:])
            if cpu in cpus:
                values = list(map(int, tokens[1:9]))
                answer[cpu] = (sum(values), values[3] + values[4])
    return answer


def idle_check(cpus: list[int], limit: float) -> dict:
    before = cpu_ticks(cpus)
    time.sleep(1)
    after = cpu_ticks(cpus)
    busy = {}
    for cpu in cpus:
        total = after[cpu][0] - before[cpu][0]
        idle = after[cpu][1] - before[cpu][1]
        busy[str(cpu)] = 1 - idle / max(total, 1)
    active = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / "comm").read_text().strip()
            if name in {"hspice", "ngspice", "calibre", "openroad", "yosys", "klayout"}:
                active.append({"pid": int(proc.name), "comm": name})
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return {"passed": max(busy.values()) <= limit and not active,
            "per_cpu_busy_fraction": busy, "limit": limit, "other_eda_processes": active,
            "scope": "one-second preflight sample, not exclusive host reservation"}


def elapsed_seconds(value: str) -> float:
    answer = 0.0
    for field in value.split(":"):
        answer = answer * 60 + float(field)
    return answer


def read_time(path: Path) -> dict:
    fields = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if ": " in line:
            key, value = line.rsplit(": ", 1)
            fields[key] = value.strip()
    result = {}
    names = {"User time (seconds)": "user_seconds", "System time (seconds)": "system_seconds",
             "Maximum resident set size (kbytes)": "max_rss_kib",
             "Voluntary context switches": "voluntary_context_switches",
             "Involuntary context switches": "involuntary_context_switches",
             "Major (requiring I/O) page faults": "major_faults",
             "Minor (reclaiming a frame) page faults": "minor_faults",
             "File system inputs": "filesystem_inputs", "File system outputs": "filesystem_outputs"}
    for key, name in names.items():
        if key in fields:
            result[name] = float(fields[key])
    elapsed = "Elapsed (wall clock) time (h:mm:ss or m:ss)"
    if elapsed in fields:
        result["wall_seconds"] = elapsed_seconds(fields[elapsed])
    if "user_seconds" in result and "system_seconds" in result:
        result["cpu_seconds"] = result["user_seconds"] + result["system_seconds"]
    return result


def group_processes(group: int) -> list[int]:
    pids = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                if os.getpgid(int(path.name)) == group:
                    pids.append(int(path.name))
            except (ProcessLookupError, PermissionError):
                continue
    return pids


def terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def framework_identity() -> dict:
    paths = [*ROOT.glob("*.py"), *ROOT.glob("edabench/*.py"), *ROOT.glob("native/*"),
             *ROOT.glob("scripts/*.sh"), *ROOT.glob("configs/*.json")]
    return {str(path.relative_to(ROOT)): sha256(path) for path in sorted(paths) if path.is_file()}


def host_snapshot(cpus: list[int]) -> dict:
    # Topology probes touch workload CPUs only outside the timed interval.
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    command = ["/usr/bin/taskset", "-c", ",".join(map(str, cpus)), sys.executable, "-c",
               "import json; from edabench.control import snapshot; "
               f"print(json.dumps(snapshot({cpus!r})))"]
    completed = subprocess.run(command, env=environment, capture_output=True, text=True,
                               check=True, timeout=30)
    return json.loads(completed.stdout)


def frequency_sample(cpus: list[int]) -> dict:
    observed = {}
    for section in Path("/proc/cpuinfo").read_text().split("\n\n"):
        fields = dict((k.strip(), v.strip()) for k, v in
                      (line.split(":", 1) for line in section.splitlines() if ":" in line))
        if fields.get("processor", "").isdigit() and int(fields["processor"]) in cpus:
            observed[fields["processor"]] = fields.get("cpu MHz")
    return {"cpu_mhz_reported": observed, "hardware_frequency_lock": False}


def audit_summary(audits: list[dict], pin_path: Path, cpus: list[int], node: int,
                  container_memory_verified: bool = False) -> dict:
    events = [json.loads(line) for line in pin_path.read_text().splitlines()] if pin_path.is_file() else []
    threads = [thread for audit in audits for thread in audit["threads"]]
    exact = audit_assignments(events, [
        {"threads": [{**thread, "pid": audit["pid"]} for thread in audit["threads"]],
         "errors": audit.get("errors", [])} for audit in audits], cpus)
    memory = [audit["numa_maps"] for audit in audits
              if audit.get("numa_maps", {}).get("status") == "CAPTURED"]
    memory_evidence = container_memory_verified or (bool(memory) and any(
        m["expected_bind_policy_vmas"] > 0 for m in memory))
    return {"passed": exact["specific_assignment_verified"] and memory_evidence,
            "process_samples": len(audits), "thread_samples": len(threads),
            "affinity_violations": exact["violations"],
            "changed_thread_assignments": exact["changed_thread_assignments"],
            "pin_events": len(events),
            "pin_events_passed": exact["pin_events_passed"], "bind_policy_evidence": memory_evidence,
            "specific_assignment_verified": exact["specific_assignment_verified"],
            "exact_assignment_audit": exact, "pin_pid_namespace": "host",
            "memory_control": "cgroup cpuset.mems" if container_memory_verified else "MPOL_BIND",
            "observed_vm_policies": sorted({p for m in memory for p in m["policy_counts"]}),
            "maximum_observed_resident_pages_outside_node": max(
                (m["resident_pages_outside_expected_node"] for m in memory), default=None),
            "limitations": "Interposer pins main/pthreads before entry; /proc audit is sampled. "
                           "MPOL_BIND readback is in console.log; shared/file pages need not migrate."}


def execute(config: dict, profile: dict, stop_ns: float | None, run: Path,
            reference: Path | None = None, kind: str = "measurement") -> dict:
    module = adapter(config["tool"])
    controls = {**config["controls"], **profile.get("controls", {})}
    cpus = controls["cpus"]
    run.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    result = {"schema_version": 1, "tool": config["tool"], "case": config["case"],
              "profile": profile, "stop_ns": stop_ns, "kind": kind, "run": str(run),
              "utc": datetime.now(timezone.utc).isoformat(), "status": "preparing",
              "controls": controls, "reference": str(reference) if reference else None}
    json_write(run / "run.json", result)
    original_affinity = os.sched_getaffinity(0)
    try:
        result["host_before"] = host_snapshot(cpus)
        os.sched_setaffinity(0, {controls["controller_cpu"]})
        if controls.get("frequency_policy") == "require-fixed":
            raise RuntimeError("Fixed-frequency certification is not implemented: request observe, or configure and verify the host policy first")
        result["frequency_control"] = {
            "mode": controls.get("frequency_policy", "observe"), "locked": False,
            "limitation": "This host exposes no cpufreq driver; MHz observations are not a frequency lock"}
        result["idle_preflight"] = idle_check(cpus, controls.get("idle_cpu_fraction_limit", 0.05))
        if not result["idle_preflight"]["passed"]:
            raise RuntimeError("Selected cores busy or another EDA job exists; no overlapping timing is permitted")
        prepared = module.prepare(run, config, profile, stop_ns)
        result.update(prepared)
        if reference:
            previous = json.loads((reference / "run.json").read_text())
            if previous["status"] != "succeeded" or previous.get("inputs") != prepared["inputs"]:
                raise RuntimeError("Reference must be successful with exactly the same input closure and endpoint")
        binary = Path(module.BINARY).resolve(strict=True)
        result["tool_identity"] = {"binary": str(binary), "sha256": sha256(binary)}
        if reference and previous.get("tool_identity") != result["tool_identity"]:
            raise RuntimeError("Tool binary changed since reference execution")
        result["staged_hashes"] = {str(path.relative_to(run)): sha256(path)
                                   for path in run.rglob("*") if path.is_file() and path.name != "run.json"}
        library = ROOT / "native/libpinthreads.so"
        if not library.is_file():
            raise RuntimeError("Build native/libpinthreads.so before running")
        environment = os.environ.copy()
        for key in list(environment):
            if key == "LD_PRELOAD" or key.startswith(("OMP_", "KMP_", "GOMP_", "MKL_")):
                environment.pop(key)
        environment.update({"PYTHONPATH": str(ROOT), "LC_ALL": "C", "LANG": "C",
                            "OMP_NUM_THREADS": str(profile.get("threads", 1)),
                            "OMP_THREAD_LIMIT": str(profile.get("threads", 1))})
        explicit_environment = {**config.get("environment", {}), **profile.get("environment", {})}
        environment.update(explicit_environment)
        result["environment_contract"] = {key: environment[key] for key in
                                          {"LC_ALL", "OMP_NUM_THREADS", "OMP_THREAD_LIMIT", *explicit_environment}}
        command = ["/usr/bin/time", "-v", "-o", str(run / "time.txt"),
                   "/usr/bin/taskset", "-c", ",".join(map(str, cpus)), sys.executable,
                   "-m", "edabench.control", "--cpus", ",".join(map(str, cpus)),
                   "--node", str(controls["memory_node"]), "--pin-library", str(library),
                   "--pin-log", str(run / "pin.jsonl"), "--", *prepared["command"]]
        result["timed_command"] = command
        result["status"] = "running"
        json_write(run / "run.json", result)
        timed_start = time.monotonic()
        observed = []
        timed_out = False
        with (run / "console.log").open("wb") as console, (run / "observations.jsonl").open("w") as observations:
            process = subprocess.Popen(command, cwd=run, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    sample = {"elapsed_seconds": time.monotonic() - timed_start, "processes": []}
                    pids = group_processes(process.pid)
                    if hasattr(module, "external_pids"):
                        pids.extend(module.external_pids(run))
                    for pid in set(pids):
                        try:
                            name = Path(f"/proc/{pid}/comm").read_text().strip()
                            if name in module.PROCESS_NAMES:
                                sample["processes"].append(control.audit_process(pid, cpus, controls["memory_node"]))
                        except (FileNotFoundError, ProcessLookupError, PermissionError):
                            continue
                    sample["frequency"] = frequency_sample(cpus)
                    observations.write(json.dumps(sample) + "\n")
                    observations.flush()
                    observed.append(sample)
                    remaining = config["timeout_seconds"] - (time.monotonic() - timed_start)
                    if remaining <= 0:
                        timed_out = True
                        terminate(process)
                        break
                    try:
                        interval = 0.05 if time.monotonic() - timed_start < 1 else 1.0
                        process.wait(timeout=min(interval, remaining))
                    except subprocess.TimeoutExpired:
                        pass
            except BaseException:
                terminate(process)
                raise
        result["monotonic_command_seconds"] = time.monotonic() - timed_start
        result["returncode"] = process.returncode
        result["resources"] = read_time(run / "time.txt")
        if hasattr(module, "resource_results"):
            result["resources"] = module.resource_results(run, result["resources"])
        result["validation"] = module.validate(run, config, stop_ns, reference)
        audits = [proc for sample in observed for proc in sample["processes"]]
        result["binding_audit"] = audit_summary(
            audits, run / "pin.jsonl", cpus, controls["memory_node"],
            result["validation"].get("container_cgroup_readback_passed", False))
        container = result["validation"].get("container_assignment_audit")
        if container is not None:
            result["binding_audit"] = {
                "passed": container["specific_assignment_verified"] and result["validation"]["container_cgroup_readback_passed"],
                "specific_assignment_verified": container["specific_assignment_verified"],
                "pin_pid_namespace": "container", "container_assignment_audit": container,
                "memory_control": "Docker cgroup cpuset.mems",
                "host_observations_only": result["binding_audit"],
                "limitations": "Host Docker-client evidence does not certify flow placement; exact flow audit uses container-local lifetimes"}
        result["status"] = ("timeout" if timed_out else "tool_failed" if process.returncode else
                            "succeeded" if result["validation"]["passed"] else "validation_failed")
        actual = result["validation"].get("simulator_statistics", {}).get("actual_threads")
        if (config["tool"] == "hspice" and actual is None) or (actual is not None and actual != profile.get("threads", actual)):
            result["status"] = "validation_failed"
            result["thread_count_mismatch"] = {"requested": profile["threads"], "actual": actual}
        if result["status"] == "succeeded" and not result["binding_audit"]["passed"]:
            result["status"] = "control_validation_failed"
        result["host_after"] = host_snapshot(cpus)
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        raise
    except Exception as error:
        result["status"] = "preflight_failed" if result["status"] == "preparing" else "runner_failed"
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if hasattr(module, "cleanup") and (run / "container.id").is_file():
            try:
                module.cleanup(run)
            except Exception as error:
                result["status"] = "cleanup_failed"
                result["cleanup_error"] = str(error)
        os.sched_setaffinity(0, original_affinity)
        result["whole_run_including_prepare_and_validation_seconds"] = time.monotonic() - started
        json_write(run / "run.json", result)
    return result


def plan(config: dict) -> list[dict]:
    rng = random.Random(config["seed"])
    baseline = next(p for p in config["profiles"] if p["role"] == "baseline")
    tasks = []
    # Complete a baseline at every endpoint before any tuning; keep warmups out of statistics.
    for profile in [baseline, *[p for p in config["profiles"] if p != baseline]]:
        for stop in config.get("stop_times_ns", [None]):
            for repeat in range(config["warmups"]):
                tasks.append({"profile": profile, "stop_ns": stop, "kind": "warmup", "repeat": repeat})
    # Randomized complete blocks limit drift without concurrent jobs or hidden sampling runs.
    for repeat in range(config["repeats"]):
        cases = [(stop, profile) for stop in config.get("stop_times_ns", [None]) for profile in config["profiles"]]
        rng.shuffle(cases)
        for stop, profile in cases:
            tasks.append({"profile": profile, "stop_ns": stop, "kind": "measurement", "repeat": repeat})
    return tasks


def campaign(config: dict, destination: Path, resume: bool = False) -> dict:
    destination = destination.resolve()
    if resume:
        previous = json.loads((destination / "campaign.json").read_text())
        if previous["config"] != config:
            raise ValueError("Cannot resume with a changed configuration")
        if previous["framework_identity"] != framework_identity():
            raise ValueError("Cannot resume with changed benchmark implementation")
    else:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    tasks = plan(config)
    metadata = {"schema_version": 1, "config": config, "plan": tasks,
                "framework_identity": framework_identity(), "status": "running", "runs": []}
    references: dict[float, Path] = {}
    with campaign_lock():
        os.sched_setaffinity(0, {config["controls"]["controller_cpu"]})
        json_write(destination / "campaign.json", metadata)
        for index, task in enumerate(tasks):
            if task["profile"]["role"] != "baseline" and task["stop_ns"] not in references:
                metadata["status"] = "blocked"
                metadata["error"] = "Tuning requires a successful baseline at the same endpoint"
                json_write(destination / "campaign.json", metadata)
                return metadata
            analysis = f'{task["stop_ns"]:g}ns' if task["stop_ns"] is not None else "full"
            run = destination / f'{index:03d}-{task["kind"]}-{analysis}-{task["profile"]["name"]}'
            if resume and (run / "run.json").is_file():
                result = json.loads((run / "run.json").read_text())
                if result["status"] in {"running", "preparing"}:
                    raise RuntimeError(f"Interrupted run retained: {run}; create a new campaign rather than overwrite it")
            else:
                print(f'[{index + 1}/{len(tasks)}] {run.name}', flush=True)
                result = execute(config, task["profile"], task["stop_ns"], run,
                                 references.get(task["stop_ns"]), task["kind"])
            metadata["runs"].append({"directory": run.name, "status": result["status"], "kind": task["kind"]})
            if result["status"] == "succeeded" and task["profile"]["role"] == "baseline":
                references.setdefault(task["stop_ns"], run)
            json_write(destination / "campaign.json", metadata)
            print(f'  {result["status"]}: {result.get("resources", {}).get("wall_seconds")} s', flush=True)
            if result["status"] in {"preflight_failed", "runner_failed", "control_validation_failed"}:
                metadata["status"] = "blocked"
                json_write(destination / "campaign.json", metadata)
                return metadata
        metadata["status"] = "completed" if all(r["status"] == "succeeded" for r in metadata["runs"]) else "completed_with_failures"
        json_write(destination / "campaign.json", metadata)
    return metadata
