"""Standalone in-container meter: Docker client CPU/RSS are not flow CPU/RSS."""

import argparse
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import time


PROC = Path("/proc")


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _cgroup_paths():
    """Resolve this process's controller directories, not the host root cgroup.

    mountinfo's root matters when Docker bind-mounts a cgroup subtree. All
    operations here are reads; no peak counters are reset or limits changed.
    """
    groups = {}
    for line in (_read(PROC / "self/cgroup") or "").splitlines():
        _, controllers, path = line.split(":", 2)
        for controller in controllers.split(","):
            groups[controller] = path
    paths = {}
    for line in (_read(PROC / "self/mountinfo") or "").splitlines():
        before, after = line.split(" - ", 1)
        fields, filesystem = before.split(), after.split()
        if filesystem[0] not in {"cgroup", "cgroup2"}:
            continue
        root, mount = (Path(value.replace("\\040", " ").replace("\\134", "\\"))
                       for value in fields[3:5])
        controllers = [""] if filesystem[0] == "cgroup2" else filesystem[2].split(",")
        for controller in controllers:
            if controller not in groups:
                continue
            group = Path(groups[controller])
            try:
                relative = group.relative_to(root)
            except ValueError:
                # A cgroup namespace renders its own root as '/'.
                if group != Path("/"):
                    continue
                relative = Path(".")
            if ".." not in relative.parts:
                paths[controller] = mount / relative
    return paths


def _counters(paths):
    values = {}
    files = {"cpuacct": ("cpuacct.usage", "cpuacct.stat"),
             "memory": ("memory.max_usage_in_bytes",),
             "cpuset": ("cpuset.cpus", "cpuset.mems"),
             "": ("cpu.stat", "memory.peak", "cpuset.cpus.effective", "cpuset.mems.effective")}
    for controller, names in files.items():
        if controller in paths:
            for name in names:
                value = _read(paths[controller] / name)
                if value is not None:
                    values[name] = value
    return values


def _cpuset_readback(values):
    return {"cpus": values.get("cpuset.cpus", values.get("cpuset.cpus.effective")),
            "mems": values.get("cpuset.mems", values.get("cpuset.mems.effective"))}


def _sample_tree():
    """Read descendants in our own PID namespace, including forks by pthreads."""
    pending = [os.getpid()]
    seen, samples, errors = set(), [], []
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        root = PROC / str(pid)
        try:
            tasks = list((root / "task").iterdir())
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            errors.append(str(error))
            continue
        for task in tasks:
            try:
                pending.extend(map(int, (task / "children").read_text().split()))
                before = (task / "stat").read_text().rsplit(")", 1)[1].split()
                sample_begin_ns = time.monotonic_ns()
                fields = dict(line.split(":", 1) for line in (task / "status").read_text().splitlines()
                              if ":" in line)
                sample_end_ns = time.monotonic_ns()
                after = (task / "stat").read_text().rsplit(")", 1)[1].split()
                if int(before[19]) != int(after[19]):
                    raise ValueError(f"Task identity changed while reading {task}")
                samples.append({"pid": pid, "tid": int(task.name), "start_ticks": int(after[19]),
                                "state": after[0],
                                "sample_begin_ns": sample_begin_ns, "sample_end_ns": sample_end_ns,
                                "allowed_cpus": fields["Cpus_allowed_list"].strip(),
                                "allowed_mems": fields["Mems_allowed_list"].strip(),
                                "rss_kib": int(fields.get("VmRSS", "0 kB").split()[0])})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError, KeyError, IndexError) as error:
                errors.append(str(error))
    return samples, errors


def _task_key(record):
    values = tuple(record.get(key) for key in ("pid", "tid", "start_ticks"))
    if any(type(value) is not int for value in values) or min(values[:2]) <= 0 or values[2] < 0:
        raise ValueError("PID/TID/start_ticks must identify a real task lifetime")
    return values


def _cpu_mask(value):
    if not isinstance(value, str):
        return value
    cpus = []
    for part in value.split(","):
        if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", part):
            return []
        ends = part.split("-")
        first, last = int(ends[0]), int(ends[-1])
        if first > last or last >= 1024:
            return []
        cpus.extend(range(first, last + 1))
    return cpus if len(cpus) == len(set(cpus)) else []


def audit_assignments(events, snapshots, cpus):
    """Exact sampled assignments in ONE PID namespace, reusable by host audits.

    Snapshots contain threads with pid/tid/start_ticks, allowed_cpus (a list or
    /proc string), and optional state. Only zombies/dead tasks and an explicitly
    recorded meter_pid are excluded. Unknown live tasks fail, even if their mask
    is a valid singleton in the pool. Never feed mixed host/container records.
    """
    assignments, first_pin_ns, untimed, seen, matched = {}, {}, set(), set(), set()
    result = {"pin_events": 0, "pin_events_passed": False, "pin_errors": [],
              "thread_samples": 0, "matched_thread_samples": 0, "exact_assignment_samples": 0,
              "startup_samples_unverified": 0, "startup_samples": [],
              "changed_assignment_samples": 0, "unrecognized_thread_samples": 0,
              "changed_thread_assignments": [], "unrecognized_threads": [],
              "violations": [], "errors": [], "error_count": 0, "conflicting_pin_assignments": []}
    pin_errors = 0
    for event in events:
        result["pin_events"] += 1
        try:
            key = _task_key(event)
            ordinal = event.get("ordinal")
            if (event.get("event") != "pin" or type(event.get("errno")) is not int or event["errno"] != 0
                    or type(ordinal) is not int or ordinal < 0 or not cpus
                    or type(event.get("cpu")) is not int
                    or type(event.get("observed_cpu")) is not int
                    or event["cpu"] != cpus[ordinal % len(cpus)]
                    or event.get("observed_cpu") != event["cpu"]):
                raise ValueError("Invalid pin result or ordinal-to-CPU assignment")
            if key in assignments and assignments[key] != event["cpu"]:
                if len(result["conflicting_pin_assignments"]) < 20:
                    result["conflicting_pin_assignments"].append(event)
                raise ValueError("Conflicting assignments for the same task lifetime")
            assignments[key] = event["cpu"]
            if "monotonic_ns" in event:
                stamp = event["monotonic_ns"]
                if type(stamp) is not int or stamp <= 0:
                    raise ValueError("Invalid pin monotonic timestamp")
                first_pin_ns[key] = min(first_pin_ns.get(key, stamp), stamp)
            else:
                untimed.add(key)
        except (AttributeError, TypeError, ValueError) as error:
            pin_errors += 1
            if len(result["pin_errors"]) < 20:
                result["pin_errors"].append({"event": event, "error": str(error)})
    result["pin_events_passed"] = bool(result["pin_events"]) and not pin_errors
    for snapshot in snapshots:
        errors = snapshot.get("errors", [])
        result["error_count"] += len(errors)
        result["errors"].extend(errors[:max(0, 20 - len(result["errors"]))])
        for thread in snapshot.get("threads", []):
            if ((snapshot.get("meter_pid") is not None and thread.get("pid") == snapshot["meter_pid"])
                    or thread.get("state", "").startswith(("Z", "X"))):
                continue
            result["thread_samples"] += 1
            detail = {**thread, "elapsed_seconds": snapshot.get("elapsed_seconds")}
            allowed = _cpu_mask(thread.get("allowed_cpus"))
            singleton = (isinstance(allowed, list) and len(allowed) == 1
                         and type(allowed[0]) is int and allowed[0] in cpus)
            try:
                key = _task_key(thread)
            except (TypeError, ValueError):
                key = None
            if key is not None:
                seen.add(key)
            begin, end = thread.get("sample_begin_ns"), thread.get("sample_end_ns")
            if "sample_begin_ns" in thread or "sample_end_ns" in thread:
                if type(begin) is not int or type(end) is not int or not 0 < begin <= end:
                    result["error_count"] += 1
                    if len(result["errors"]) < 20:
                        result["errors"].append("Invalid affinity sample monotonic interval")
            # Exclude only a fully bracketed observation before the first pin.
            # It precedes the trampoline/constructor control boundary, not a
            # post-entry reassignment. Never infer this for old untimed records.
            startup = (key in first_pin_ns and key not in untimed
                       and type(begin) is int and type(end) is int
                       and 0 < begin <= end < first_pin_ns[key])
            within_pool = (isinstance(allowed, list) and bool(allowed)
                           and all(type(cpu) is int and cpu in cpus for cpu in allowed))
            if (not within_pool or (not startup and not singleton)) and len(result["violations"]) < 20:
                result["violations"].append(detail)
            if startup:
                result["startup_samples_unverified"] += 1
                if len(result["startup_samples"]) < 20:
                    result["startup_samples"].append({**detail, "first_pin_ns": first_pin_ns[key]})
                continue
            if key not in assignments:
                result["unrecognized_thread_samples"] += 1
                if len(result["unrecognized_threads"]) < 20:
                    result["unrecognized_threads"].append(detail)
                continue
            result["matched_thread_samples"] += 1
            matched.add(key)
            expected = assignments[key]
            if singleton and allowed == [expected]:
                result["exact_assignment_samples"] += 1
            else:
                result["changed_assignment_samples"] += 1
                if len(result["changed_thread_assignments"]) < 20:
                    result["changed_thread_assignments"].append({**detail, "expected_cpu": expected})
    total = result["thread_samples"]
    controlled = total - result["startup_samples_unverified"]
    result.update({"identity_key": ["pid", "tid", "start_ticks"], "same_pid_namespace_required": True,
                   "unique_sampled_tasks": len(seen), "unique_matched_tasks": len(matched),
                   "unique_pinned_tasks": len(assignments), "pinned_tasks_without_sample": len(set(assignments) - matched),
                   "sampled_exact_assignment_coverage": result["exact_assignment_samples"] / total if total else 0,
                   "sampled_identity_coverage": result["matched_thread_samples"] / total if total else 0,
                   "post_pin_thread_samples": controlled,
                   "post_pin_exact_assignment_coverage": result["exact_assignment_samples"] / controlled if controlled else 0,
                   "specific_assignment_verified": bool(controlled) and result["exact_assignment_samples"] == controlled
                                                   and result["pin_events_passed"] and not result["error_count"]
                                                   and not result["violations"],
                   "scope": "Exact assignments for sampled live post-pin tasks only; not continuous execution proof. "
                            "Proven pre-first-pin samples and unsampled tasks are reported as unverified. "
                            "Unknown live tasks, ambiguous transition samples and post-pin reassignment fail closed."})
    return result


def assignment_audit(pin_path, readback_path, cpus):
    errors = []
    try:
        events = [json.loads(line) for line in Path(pin_path).read_text().splitlines()]
    except (OSError, TypeError, ValueError) as error:
        events = []
        errors.append(f"Pin log unavailable or invalid: {error}")

    def snapshots():
        yield {"errors": errors}
        try:
            with Path(readback_path).open() as stream:
                for line in stream:
                    sample = json.loads(line)
                    if (not isinstance(sample, dict) or not isinstance(sample.get("threads"), list)
                            or not isinstance(sample.get("errors", []), list)
                            or any(not isinstance(thread, dict) or not isinstance(thread.get("state", ""), str)
                                   for thread in sample["threads"])):
                        raise ValueError("Invalid affinity snapshot")
                    yield sample
        except (OSError, TypeError, ValueError) as error:
            yield {"errors": [f"Affinity readback unavailable or invalid: {error}"]}

    return {**audit_assignments(events, snapshots(), cpus), "readback_file": str(readback_path),
            "pin_log": str(pin_path), "sample_interval_seconds": 0.1}


def _cpu_times(before, after, waited):
    user, system = waited
    source = "waited process tree RUSAGE_CHILDREN"
    cpu = user + system
    if "cpuacct.usage" in before and "cpuacct.usage" in after:
        cpu = (int(after["cpuacct.usage"]) - int(before["cpuacct.usage"])) / 1e9
        source = "container cgroup v1 cpuacct delta, including meter"
        if "cpuacct.stat" in before and "cpuacct.stat" in after:
            a = dict(line.split() for line in before["cpuacct.stat"].splitlines())
            b = dict(line.split() for line in after["cpuacct.stat"].splitlines())
            ticks = os.sysconf("SC_CLK_TCK")
            user, system = ((int(b[key]) - int(a[key])) / ticks for key in ("user", "system"))
            source += "; user/system from cpuacct.stat (tick resolution)"
        else:
            source += "; user/system fallback to waited process tree"
    elif "cpu.stat" in before and "cpu.stat" in after:
        a = dict(line.split() for line in before["cpu.stat"].splitlines())
        b = dict(line.split() for line in after["cpu.stat"].splitlines())
        cpu, user, system = ((int(b[key]) - int(a[key])) / 1e6
                             for key in ("usage_usec", "user_usec", "system_usec"))
        source = "container cgroup v2 cpu.stat delta, including meter"
    return {"cpu_seconds": cpu, "user_seconds": user, "system_seconds": system,
            "cpu_accounting_source": source}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    audit_path = args.output.with_name("container-affinity.jsonl")
    if not command or args.output.exists() or audit_path.exists():
        parser.error("A command and a new output path are required")
    started = time.monotonic()
    paths = _cgroup_paths()
    counters_before = _counters(paths)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpus = list(map(int, os.environ.get("EDABENCH_PIN_CPUS", "").split(","))) if os.environ.get("EDABENCH_PIN_CPUS") else []
    peak_tree_rss = 0
    remaining = []
    launch_error = None
    with audit_path.open("x") as audit:
        try:
            process = subprocess.Popen(command)
        except OSError as error:
            process = None
            launch_error = str(error)
        while process is not None:
            samples, errors = _sample_tree()
            elapsed = time.monotonic() - started
            audit.write(json.dumps({"elapsed_seconds": elapsed, "meter_pid": os.getpid(),
                                    "threads": samples, "errors": errors}) + "\n")
            audit.flush()
            rss = {}
            for thread in samples:
                if thread["pid"] == os.getpid() or thread["state"].startswith("Z"):
                    continue
                rss[thread["pid"]] = thread["rss_kib"]
            peak_tree_rss = max(peak_tree_rss, sum(rss.values()))
            if process.poll() is not None:
                remaining = [pid for pid in rss if pid != process.pid]
                break
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    counters_after = _counters(paths)
    returncode = process.returncode if process is not None else 127
    wall_seconds = time.monotonic() - started
    summary = assignment_audit(os.environ.get("EDABENCH_PIN_LOG"), audit_path, cpus)
    data = {"wall_seconds": wall_seconds,
            **_cpu_times(counters_before, counters_after,
                         (after.ru_utime - before.ru_utime, after.ru_stime - before.ru_stime)),
            "max_rss_kib": after.ru_maxrss, "returncode": returncode,
            "launch_error": launch_error, "remaining_processes": remaining,
            "sampled_tree_max_rss_kib": peak_tree_rss,
            "affinity_audit": summary,
            "cgroup_paths": {key: str(value) for key, value in paths.items()},
            "cgroup_counters_before": counters_before, "cgroup_counters_after": counters_after,
            "cgroup_cpuset_readback": [_cpuset_readback(values) for values in (counters_before, counters_after)],
            "cgroup_memory_peak_bytes": int(counters_after.get("memory.max_usage_in_bytes",
                                                                  counters_after.get("memory.peak", "0"))) or None,
            "scope": "CPU source is explicit; cgroup deltas include meter overhead, not Docker client. "
                     "max_rss_kib is waited tree peak individual-process RSS, NOT simultaneous tree RSS. "
                     "sampled_tree_max_rss_kib sums per-process RSS (shared pages double-counted; short peaks missed). "
                     "cgroup memory peak is container lifetime charged memory including cache, NOT RSS."}
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(args.output)
    return returncode if returncode >= 0 else 128 - returncode


if __name__ == "__main__":
    raise SystemExit(main())
