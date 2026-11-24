"""Process-local Linux benchmark controls; no privileged or global writes.

See docs/controls.md for the snapshot/audit schemas and their limitations.
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


CPU_ROOT = Path("/sys/devices/system/cpu")
NODE_ROOT = Path("/sys/devices/system/node")
MPOL_BIND = 2


def parse_cpu_list(value: str) -> list[int]:
    """Parse Linux list notation, rejecting empty, reversed and duplicate items."""
    result: list[int] = []
    for item in value.strip().split(","):
        if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", item):
            raise ValueError(f"invalid CPU/node list: {value!r}")
        ends = [int(part) for part in item.split("-")]
        start, end = ends[0], ends[-1]
        if end < start or end > 1_048_575:
            raise ValueError(f"invalid CPU/node range: {item}")
        result.extend(range(start, end + 1))
    if len(result) != len(set(result)):
        raise ValueError("duplicate CPU/node in list")
    return result


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _status_fields(path: Path) -> dict[str, str]:
    return dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)


def validate_cpus(cpus: list[int], *, require_current_affinity: bool = True) -> list[int]:
    """Preserve requested order (the interposer's round-robin order)."""
    if not cpus or any(type(cpu) is not int or cpu < 0 for cpu in cpus):
        raise ValueError("cpus must be a nonempty list of nonnegative integers")
    if len(cpus) != len(set(cpus)):
        raise ValueError("duplicate CPU")
    online = set(parse_cpu_list((CPU_ROOT / "online").read_text()))
    allowed = set(os.sched_getaffinity(0))
    if set(cpus) - online:
        raise ValueError(f"CPUs not online: {sorted(set(cpus) - online)}")
    if require_current_affinity and set(cpus) - allowed:
        raise ValueError(f"CPUs outside current allowed affinity: {sorted(set(cpus) - allowed)}")
    return list(cpus)


def _validate_node(node: int) -> None:
    if type(node) is not int or node < 0:
        raise ValueError("node must be a nonnegative integer")
    online = parse_cpu_list((NODE_ROOT / "online").read_text())
    allowed = parse_cpu_list(_status_fields(Path("/proc/self/status"))["Mems_allowed_list"].strip())
    if node not in online or node not in allowed:
        raise ValueError(f"NUMA node {node} is not both online and allowed; online={online}, allowed={allowed}")
    meminfo = (NODE_ROOT / f"node{node}" / "meminfo").read_text()
    match = re.search(r"MemTotal:\s+(\d+)", meminfo)
    if match is None or int(match[1]) == 0:
        raise ValueError(f"NUMA node {node} has no reported memory")


def _mempolicy_functions():
    """Use checked int-returning interfaces, never libnuma's void warning API."""
    libc = ctypes.CDLL(None, use_errno=True)
    word_pointer = ctypes.POINTER(ctypes.c_ulong)
    for library, name in ((libc, "libc"), (None, "libnuma.so.1")):
        if library is None:
            try:
                library = ctypes.CDLL(name, use_errno=True)
            except OSError:
                continue
        if hasattr(library, "set_mempolicy") and hasattr(library, "get_mempolicy"):
            setter, getter = library.set_mempolicy, library.get_mempolicy
            setter.argtypes = [ctypes.c_int, word_pointer, ctypes.c_ulong]
            getter.argtypes = [ctypes.POINTER(ctypes.c_int), word_pointer,
                               ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
            setter.restype = getter.restype = ctypes.c_int
            return setter, getter, name
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise OSError(errno.ENOSYS, "no checked mempolicy interface for this architecture")
    syscall = libc.syscall
    syscall.restype = ctypes.c_long

    def setter(mode, mask, maxnode):
        return syscall(ctypes.c_long(238), ctypes.c_int(mode), mask, ctypes.c_ulong(maxnode))

    def getter(mode, mask, maxnode, address, flags):
        return syscall(ctypes.c_long(239), mode, mask, ctypes.c_ulong(maxnode),
                       ctypes.c_void_p(address), ctypes.c_ulong(flags))

    return setter, getter, "libc.syscall(x86_64)"


def bind_memory(node: int) -> dict:
    """Set and read back this calling thread's default MPOL_BIND policy.

    Call in the single-threaded launcher, not in a multi-threaded runner.
    Descendant threads/exec inherit it. Existing pages are not migrated.
    """
    _validate_node(node)
    online = parse_cpu_list((NODE_ROOT / "online").read_text())
    bits = ctypes.sizeof(ctypes.c_ulong) * 8
    # CentOS 7 get_mempolicy requires MAX_NUMNODES bits, even when only eight
    # nodes are possible. /proc exposes that full compile-time mask width.
    possible = _read(NODE_ROOT / "possible")
    status = _status_fields(Path("/proc/self/status"))
    kernel_mask_bits = len(status["Mems_allowed"].strip().replace(",", "")) * 4
    maxnode = max(max(parse_cpu_list(possible) if possible else online) + 1, kernel_mask_bits)
    words = (maxnode + bits - 1) // bits
    mask = (ctypes.c_ulong * words)()
    mask[node // bits] |= 1 << (node % bits)
    setter, getter, backend = _mempolicy_functions()
    ctypes.set_errno(0)
    if setter(MPOL_BIND, mask, maxnode) != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, f"set_mempolicy(MPOL_BIND, node={node}): {os.strerror(error)}")
    observed = (ctypes.c_ulong * words)()
    mode = ctypes.c_int()
    ctypes.set_errno(0)
    if getter(ctypes.byref(mode), observed, maxnode, None, 0) != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, f"get_mempolicy verification: {os.strerror(error)}")
    nodes = [i for i in range(maxnode) if observed[i // bits] & (1 << (i % bits))]
    if mode.value != MPOL_BIND or nodes != [node]:
        raise RuntimeError(f"memory policy readback mismatch: mode={mode.value}, nodes={nodes}")
    return {"status": "VERIFIED", "policy": "MPOL_BIND", "nodes": nodes, "backend": backend,
            "scope": "calling thread default policy, inherited across pthread/fork/exec; no migration"}


def _frequency_lock(cpus: list[int]) -> dict:
    missing = [cpu for cpu in cpus if not (CPU_ROOT / f"cpu{cpu}" / "cpufreq").is_dir()]
    reason = ("cpufreq directory/driver absent for selected CPUs" if len(missing) == len(cpus)
              else "this suite does not establish or verify a hardware frequency lock")
    return {"status": "UNAVAILABLE", "reason": reason, "attempted": False,
            "cpus_without_cpufreq": missing,
            "reported_mhz_is_fixed_frequency_evidence": False}


def _cpuid_snapshot(cpus: list[int]) -> dict:
    helper = os.environ.get("EDABENCH_CPUID_HELPER")
    path = Path(helper) if helper else Path(__file__).resolve().parents[1] / "native" / "cpuid-topology"
    if not path.is_file() or not os.access(path, os.X_OK):
        return {"status": "UNAVAILABLE", "reason": f"compile CPUID helper or set EDABENCH_CPUID_HELPER: {path}"}
    try:
        run = subprocess.run([str(path.resolve()), "--cpus", ",".join(map(str, cpus))],
                             capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(run.stdout)
        if [row["cpu"] for row in data["cpus"]] != cpus:
            raise ValueError("helper did not sample exactly the requested CPUs in order")
        return {"status": "CAPTURED", "helper": str(path.resolve()), "evidence": data}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return {"status": "ERROR", "reason": str(exc)}


def snapshot(cpus: list[int]) -> dict:
    """Read-only evidence for a runner; never claims sysfs IDs are physical cores."""
    # A controller may intentionally live on a housekeeping CPU outside the
    # measured pool. Only the helper child widens its own affinity, with exact
    # kernel readback checking online/cpuset limits; the controller never does.
    cpus = validate_cpus(cpus, require_current_affinity=False)
    status = _status_fields(Path("/proc/self/status"))
    cpuinfo: dict[int, dict[str, str]] = {}
    for section in Path("/proc/cpuinfo").read_text().strip().split("\n\n"):
        fields = dict((key.strip(), value.strip()) for key, value in
                      (line.split(":", 1) for line in section.splitlines() if ":" in line))
        if "processor" in fields:
            cpuinfo[int(fields["processor"])] = fields
    topology, frequency = {}, {}
    for cpu in cpus:
        root = CPU_ROOT / f"cpu{cpu}"
        topology[str(cpu)] = {
            "topology_reported": {name: _read(root / "topology" / name) for name in
                                  ("physical_package_id", "die_id", "core_id",
                                   "thread_siblings_list", "core_siblings_list")},
            "numa_nodes_reported": sorted(int(p.name[4:]) for p in root.glob("node[0-9]*")),
        }
        frequency[str(cpu)] = {
            "cpu_mhz_reported": cpuinfo.get(cpu, {}).get("cpu MHz"),
            "model_name_reported": cpuinfo.get(cpu, {}).get("model name"),
            "cpufreq": {name: _read(root / "cpufreq" / name) for name in
                        ("scaling_driver", "scaling_governor", "scaling_min_freq", "scaling_max_freq",
                         "scaling_cur_freq", "cpuinfo_min_freq", "cpuinfo_max_freq")},
        }
    uname = os.uname()
    return {
        "schema_version": 1, "requested_cpus": cpus,
        "kernel": dict(zip(("sysname", "nodename", "release", "version", "machine"), uname)),
        "constraints": {"online_cpus": parse_cpu_list((CPU_ROOT / "online").read_text()),
                        "allowed_cpus": sorted(os.sched_getaffinity(0)),
                        "allowed_cpus_scope": "caller's current sched affinity, not cgroup allocation",
                        "cpus_allowed_list_reported": status["Cpus_allowed_list"].strip(),
                        "mems_allowed_list_reported": status["Mems_allowed_list"].strip(),
                        "cgroup_membership": _read(Path("/proc/self/cgroup"))},
        "numa": {"online_nodes": _read(NODE_ROOT / "online"),
                 "nodes": {p.name: {name: _read(p / name) for name in ("cpulist", "distance", "meminfo")}
                           for p in sorted(NODE_ROOT.glob("node[0-9]*"))}},
        "sysfs": topology, "frequency_reported": frequency,
        "frequency_lock": _frequency_lock(cpus), "cpuid": _cpuid_snapshot(cpus),
        "limitations": ["Sysfs/lscpu package and core IDs are untrusted reported data, not physical topology.",
                        "CPUID records are hardware-reported evidence, not an assumed physical core count.",
                        "Snapshot requires online CPUs, not membership in controller affinity; helper checks kernel constraints.",
                        "CPU MHz is a reported sample, not proof of a fixed clock or boost state.",
                        "Snapshot is not atomic; hotplug/cpuset constraints may change after capture."],
    }


def audit_process(pid: int, cpus: list[int], node: int) -> dict:
    """Best-effort, non-atomic remote /proc evidence; no mutation or ptrace."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be positive")
    if not cpus or any(type(cpu) is not int or cpu < 0 for cpu in cpus):
        raise ValueError("cpus must be nonempty nonnegative integers")
    if type(node) is not int or node < 0:
        raise ValueError("node must be nonnegative")
    root = Path(f"/proc/{pid}")
    result = {"schema_version": 1, "pid": pid, "expected_cpus": list(cpus), "expected_node": node,
              "threads": [], "errors": [], "vanished_threads": [], "memory_binding_verified": False,
              "limitations": ["Threads/maps can change or disappear during this non-atomic read.",
                              "Allowed masks show permission, not historical CPU residency.",
                              "numa_maps records VMA policies and resident pages, not remote thread default policies.",
                              "Old/shared/file-backed pages need not move under MPOL_BIND; counts are pages, not bytes.",
                              "No resident pages or bind lines is not proof of success; target can change its own controls."]}
    try:
        tasks = sorted((root / "task").iterdir(), key=lambda p: int(p.name))
    except OSError as exc:
        tasks = []
        result["errors"].append(str(exc))
    for task in tasks:
        try:
            before = (task / "stat").read_text().rsplit(")", 1)[1].split()
            sample_begin_ns = time.monotonic_ns()
            fields = _status_fields(task / "status")
            sample_end_ns = time.monotonic_ns()
            after = (task / "stat").read_text().rsplit(")", 1)[1].split()
            if int(before[19]) != int(after[19]):
                raise ValueError("task identity changed during affinity readback")
            allowed = parse_cpu_list(fields["Cpus_allowed_list"].strip())
            result["threads"].append({"tid": int(task.name), "name": fields["Name"].strip(),
                                      "start_ticks": int(after[19]), "state": after[0],
                                      "sample_begin_ns": sample_begin_ns, "sample_end_ns": sample_end_ns,
                                      "allowed_cpus": allowed,
                                      "mems_allowed_list": fields["Mems_allowed_list"].strip(),
                                      "within_requested_cpus": bool(allowed) and set(allowed) <= set(cpus),
                                      "single_cpu": len(allowed) == 1})
        except (FileNotFoundError, ProcessLookupError):
            result["vanished_threads"].append(int(task.name))
        except (OSError, ValueError, KeyError, IndexError) as exc:
            result["errors"].append(f"tid {task.name}: {exc}")
    result["affinity_within_requested"] = bool(result["threads"]) and not result["errors"] and all(
        thread["within_requested_cpus"] for thread in result["threads"])
    result["all_threads_single_cpu"] = bool(result["threads"]) and not result["errors"] and all(
        thread["single_cpu"] for thread in result["threads"])
    try:
        raw = (root / "numa_maps").read_text()
        policies: collections.Counter = collections.Counter()
        pages: collections.Counter = collections.Counter()
        mappings = []
        for line in raw.splitlines():
            fields = line.split()
            policy = fields[1] if len(fields) > 1 else "UNKNOWN"
            resident = {int(match[1]): int(match[2]) for field in fields
                        if (match := re.fullmatch(r"N(\d+)=(\d+)", field))}
            policies[policy] += 1
            pages.update(resident)
            mappings.append({"address": fields[0], "policy": policy, "resident_pages_by_node": resident})
        result["numa_maps"] = {"status": "CAPTURED", "raw": raw, "mappings": mappings,
                               "policy_counts": dict(policies), "resident_pages_by_node": dict(pages),
                               "expected_bind_policy_vmas": policies[f"bind:{node}"],
                               "resident_pages_outside_expected_node": sum(v for k, v in pages.items() if k != node)}
    except (FileNotFoundError, ProcessLookupError) as exc:
        result["numa_maps"] = {"status": "VANISHED", "reason": str(exc)}
    except OSError as exc:
        result["numa_maps"] = {"status": "UNAVAILABLE", "reason": str(exc)}
        result["errors"].append(str(exc))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpus", required=True, help="ordered CPU list, e.g. 0,1 or 0-3")
    parser.add_argument("--node", required=True, type=int)
    parser.add_argument("--pin-library", type=Path)
    parser.add_argument("--pin-log", type=Path)
    parser.add_argument("--require-fixed", "--require-fixed-frequency", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command after -- is required")
    if bool(args.pin_library) != bool(args.pin_log):
        parser.error("--pin-library and --pin-log must be specified together")
    try:
        cpus = validate_cpus(parse_cpu_list(args.cpus))
        _validate_node(args.node)
        frequency = _frequency_lock(cpus)
        print(json.dumps({"event": "edabench_frequency_lock", **frequency}), file=sys.stderr, flush=True)
        if args.require_fixed:
            raise RuntimeError(f"fixed frequency required but UNAVAILABLE: {frequency['reason']}")
        env = os.environ.copy()
        if args.pin_library:
            library = str(args.pin_library.resolve(strict=True))
            if not Path(library).is_file() or not os.access(library, os.R_OK):
                raise ValueError(f"pin library is not a readable file: {library}")
            if any(char.isspace() or char == ":" for char in library):
                raise ValueError("LD_PRELOAD library paths cannot contain whitespace or colons")
            if max(cpus) >= 1024:
                raise ValueError("pinthreads uses glibc CPU_SETSIZE=1024; CPU must be below 1024")
            env["LD_PRELOAD"] = library + (" " + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
            env["EDABENCH_PIN_CPUS"] = ",".join(map(str, cpus))
            env["EDABENCH_PIN_LOG"] = str(args.pin_log.resolve())
        elif env.get("EDABENCH_PIN_CPUS") or env.get("EDABENCH_PIN_LOG"):
            raise ValueError("stale EDABENCH_PIN_* environment: use explicit --pin-library/--pin-log")
        os.sched_setaffinity(0, cpus)
        if set(os.sched_getaffinity(0)) != set(cpus):
            raise RuntimeError("sched_setaffinity readback mismatch")
        policy = bind_memory(args.node)
        print(json.dumps({"event": "edabench_controls", "cpus": cpus, "memory": policy,
                          "pin_library": str(args.pin_library.resolve()) if args.pin_library else None}),
              file=sys.stderr, flush=True)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"edabench.control: {exc}", file=sys.stderr)
        return 125
    try:
        os.execvpe(command[0], command, env)
    except FileNotFoundError as exc:
        print(f"edabench.control: exec: {exc}", file=sys.stderr)
        return 127
    except OSError as exc:
        print(f"edabench.control: exec: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
