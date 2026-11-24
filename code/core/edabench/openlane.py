"""OpenLane adapter: cgroup CPU/NUMA limits must be applied inside Docker."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .container_meter import assignment_audit
from .inputs import json_write, sha256

DOCKER = "/opt/eda_public/bin/eda-docker"
BINARY = "/opt/vendor/openlane/2.3.10/image.txt"
PROCESS_NAMES = {"openlane", "python3", "python", "openroad", "yosys", "magic", "klayout"}
_PROC = Path("/proc")
_LIVE = {}
_INSPECT_ERRORS = {}


def _docker_json(arguments: list[str]) -> dict:
    probe = subprocess.run([DOCKER, *arguments], capture_output=True, text=True,
                           timeout=10, check=True)
    return json.loads(probe.stdout)[0]


def _cpu_set(value: str) -> set[int]:
    result = set()
    if not value:
        return result
    for part in value.split(","):
        ends = part.split("-")
        first, last = int(ends[0]), int(ends[-1])
        if len(ends) > 2 or first < 0 or last < first:
            raise ValueError(f"Invalid CPU/node list: {value}")
        result.update(range(first, last + 1))
    return result


def prepare(run: Path, config: dict, profile: dict, stop_ns: None) -> dict:
    controls = {**config["controls"], **profile.get("controls", {})}
    environment = {"HOME": "/eda-home", "PDK_ROOT": "/opt/eda_public/pdks",
                   "LC_ALL": "C", "LANG": "C", "OMP_NUM_THREADS": str(profile["threads"]),
                   "OMP_THREAD_LIMIT": str(profile["threads"])}
    explicit = {**config.get("environment", {}), **profile.get("environment", {})}
    for key, value in explicit.items():
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in {"LD_PRELOAD", "PYTHONPATH"}
                or key.startswith("EDABENCH_") or not isinstance(value, str) or "\0" in value):
            raise ValueError(f"Invalid or reserved container environment variable: {key}")
    environment.update(explicit)
    image = Path(BINARY).read_text().strip()
    if not re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", image):
        raise ValueError("OpenLane image must be pinned by sha256 digest, not a mutable tag")
    image_info = _docker_json(["image", "inspect", image])
    image_id = image_info["Id"]
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or ("@" in image and image not in image_info.get("RepoDigests", []))
            or ("@" not in image and image != image_id)):
        raise ValueError("Local image identity does not match the configured immutable pin")
    source = Path(config["source_directory"]).resolve(strict=True)
    files = []
    for path in source.rglob("*"):
        if path.is_file():
            relative = path.relative_to(source)
            target = run / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            files.append({"path": str(path), "sha256": sha256(path)})
    library = Path(__file__).resolve().parents[1] / "native/libpinthreads.so"
    shutil.copyfile(library, run / "libpinthreads.so")
    shutil.copyfile(Path(__file__).with_name("container_meter.py"), run / "container_meter.py")
    cpus = ",".join(map(str, controls["cpus"]))
    environment.update({"LD_PRELOAD": f"{run}/libpinthreads.so", "EDABENCH_PIN_CPUS": cpus,
                        "EDABENCH_PIN_LOG": f"{run}/pin-container.jsonl"})
    contract = {"image_reference": image, "image_id": image_id,
                "repo_digests": image_info.get("RepoDigests", []),
                "cpus": cpus, "memory_node": str(controls["memory_node"]),
                "user": f"{os.getuid()}:{os.getgid()}", "environment": environment}
    json_write(run / "container-contract.json", contract)
    _LIVE.pop(str(run), None)
    command = [DOCKER, "run", "--pull=never", "--cidfile", str(run / "container.id"),
               "--user", contract["user"], "--network", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--cpuset-cpus", cpus,
               "--cpuset-mems", str(controls["memory_node"]), "--tmpfs", "/eda-home:rw,mode=1777",
               *[arg for key, value in sorted(environment.items()) for arg in ("-e", f"{key}={value}")],
               "-v", "/opt/eda_public:/opt/eda_public:ro", "-v", f"{run}:{run}", "-w", str(run),
               image_id, "python3", str(run / "container_meter.py"), "--output",
               str(run / "container-resources.json"), "--", "openlane", "--manual-pdk", "--pdk", config.get("pdk", "sky130A"),
               "--jobs", str(profile["threads"]), "--run-tag", "benchmark", config["design_config"],
               *profile.get("cli", [])]
    return {"command": command, "inputs": {"files": files, "image": image, "image_id": image_id},
            "output_contract": "Complete configured Classic flow, final GDS and DRC/LVS metrics",
            "container_controls": {**contract, "memory_node": controls["memory_node"],
                                   "policy": "Docker cgroup cpuset.mems; not inherited host MPOL_BIND"}}


def _inspect(run: Path) -> dict | None:
    cid = run / "container.id"
    if not cid.is_file() or not cid.read_text().strip():
        return None
    try:
        info = _docker_json(["inspect", cid.read_text().strip()])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        _INSPECT_ERRORS[str(run)] = str(error)
        return None
    _INSPECT_ERRORS.pop(str(run), None)
    return info


def _process_identity(pid: int) -> tuple[str, str] | None:
    try:
        root = _PROC / str(pid)
        # Field 22; comm (field 2) may itself contain spaces and parentheses.
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X"}:
            return None
        return fields[19], os.readlink(root / "ns/pid")
    except (OSError, IndexError):
        return None


def external_pids(run: Path) -> list[int]:
    """Host IDs only. Cache inspect while init's start time/namespace stay live.

    No namespace-local pin IDs are ever added to the host pin log. CentOS 7
    lacks NSpid; neither a numerical match nor a start-time match is a safe
    namespace mapping. The meter provides independent same-namespace readback.
    """
    cid_path = run / "container.id"
    if not cid_path.is_file() or not (cid := cid_path.read_text().strip()):
        return []
    cached = _LIVE.get(str(run), {})
    root = cached.get("pid", 0)
    identity = _process_identity(root) if root else None
    if cached.get("cid") != cid or not identity or identity != cached.get("identity"):
        if cached.get("cid") == cid and time.monotonic() < cached.get("retry_after", 0):
            return []
        info = _inspect(run)
        root = info["State"]["Pid"] if info and info["State"]["Running"] else 0
        identity = _process_identity(root) if root else None
        if identity:
            try:
                groups = (_PROC / str(root) / "cgroup").read_text().splitlines()
                components = {part for line in groups for part in line.split(":", 2)[2].split("/")}
                # inspect and /proc are non-atomic: do not attach to an unrelated
                # process that reused Docker's reported init PID after exit.
                if cid not in components and f"docker-{cid}.scope" not in components:
                    identity = None
            except (OSError, IndexError):
                identity = None
        cached = {"cid": cid, "pid": root, "identity": identity,
                  "retry_after": time.monotonic() + 1}
        _LIVE[str(run)] = cached
    if not identity:
        return []
    pending = [root]
    seen = set()
    pids = []
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        current = _process_identity(pid)
        if not current or current[1] != identity[1]:
            continue
        pids.append(pid)
        try:
            # A non-main pthread may fork; its children are not in main/children.
            for task in (_PROC / str(pid) / "task").iterdir():
                try:
                    pending.extend(map(int, (task / "children").read_text().split()))
                except OSError:
                    pass
        except OSError:
            pass
    return pids if _process_identity(root) == identity else []


def validate(run: Path, config: dict, stop_ns: None, reference: Path | None = None) -> dict:
    # Always fresh at validation, even when polling used a live-init cache.
    info = _inspect(run)
    contract_path = run / "container-contract.json"
    contract = json.loads(contract_path.read_text()) if contract_path.is_file() else {}
    resources_path = run / "container-resources.json"
    resource_error = None
    try:
        resources = json.loads(resources_path.read_text())
        if not isinstance(resources, dict):
            raise ValueError("Invalid container resource object")
    except (OSError, ValueError) as error:
        resources = {}
        resource_error = str(error)
    host = info.get("HostConfig", {}) if info else {}
    actual_env = dict(entry.split("=", 1) for entry in info.get("Config", {}).get("Env", [])
                      if "=" in entry) if info else {}
    image_match = bool(contract and info) and info.get("Image") == contract["image_id"]
    env_match = bool(contract and info) and all(actual_env.get(key) == value
                                               for key, value in contract["environment"].items())
    docker_cpuset = bool(contract and info) and (
        _cpu_set(host.get("CpusetCpus", "")) == _cpu_set(contract["cpus"])
        and _cpu_set(host.get("CpusetMems", "")) == _cpu_set(contract["memory_node"]))
    kernel_reads = resources.get("cgroup_cpuset_readback", [])
    kernel_cpuset = bool(contract and len(kernel_reads) == 2) and all(
        read.get("cpus") is not None and read.get("mems") is not None
        and _cpu_set(read["cpus"]) == _cpu_set(contract["cpus"])
        and _cpu_set(read["mems"]) == _cpu_set(contract["memory_node"]) for read in kernel_reads)
    cgroup_match = bool(docker_cpuset and kernel_cpuset)
    isolation_match = bool(info and contract) and (
        host.get("NetworkMode") == "none" and host.get("PidMode", "") in {"", "private"}
        and "ALL" in {cap.upper() for cap in host.get("CapDrop", [])}
        and any(opt in {"no-new-privileges", "no-new-privileges:true"}
                for opt in host.get("SecurityOpt", []))
        and info.get("Config", {}).get("User") == contract["user"])
    inspection = {"expected": contract, "actual": info,
                  "inspection_error": _INSPECT_ERRORS.get(str(run)),
                  "image_identity_passed": image_match, "environment_passed": env_match,
                  "docker_cpuset_passed": docker_cpuset, "kernel_cpuset_passed": kernel_cpuset,
                  "isolation_passed": isolation_match}
    json_write(run / "container-inspection.json", inspection)
    pin = run / "pin-container.jsonl"
    # DO NOT copy these events into pin.jsonl: audit_summary indexes that log by
    # host PID/TID. Namespace-local IDs can even collide with unrelated host IDs.
    # Recompute from raw same-namespace evidence, not a trusted meter summary.
    cpus = list(map(int, contract["cpus"].split(","))) if contract else []
    affinity = assignment_audit(pin, run / "container-affinity.jsonl", cpus)
    affinity_pass = affinity["specific_assignment_verified"]
    assignment = {**affinity, "pin_pid_namespace": "container",
                  "host_pid_mapping": "NOT REQUIRED: pin records and readback use the same container PID namespace",
                  "sampled_singleton_pool_passed": bool(affinity["thread_samples"]) and not affinity["violations"],
                  "same_namespace_affinity": affinity,
                  "limitation": affinity["scope"]}
    metrics_path = run / "runs/benchmark/final/metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    keys = config["zero_metric_keys"]
    checks = {key: metrics.get(key) for key in keys}
    gds = list((run / "runs/benchmark/final/gds").glob("*.gds"))
    state = info.get("State", {}) if info else {}
    completed = bool(state) and not state.get("Running", True) and state.get("ExitCode") == 0 and not state.get("OOMKilled", False)
    metered = bool(resources) and resources.get("returncode") == 0 and not resources.get("remaining_processes")
    return {"passed": bool(gds) and bool(metrics) and all(value == 0 for value in checks.values())
                      and cgroup_match and image_match and env_match and isolation_match and completed
                      and metered and affinity_pass,
            "reason": None if metrics else "missing final flow metrics",
            "zero_checks": checks, "gds": [{"path": str(p.relative_to(run)), "sha256": sha256(p)} for p in gds],
            "container_cgroup_readback_passed": cgroup_match,
            "container_image_identity_passed": image_match, "container_environment_passed": env_match,
            "container_isolation_passed": isolation_match, "container_completed": completed,
            "container_resources_complete": metered, "container_assignment_audit": assignment,
            "container_resource_error": resource_error,
            "container_contract": contract, "kernel_cpuset_readback": kernel_reads,
            "scope": "Full configured flow; not production signoff or certified IR-drop"}


def resource_results(run: Path, outer: dict) -> dict:
    counters = ("user_seconds", "system_seconds", "cpu_seconds", "max_rss_kib")
    result = {**outer, **{f"client_{key}": outer.get(key) for key in counters},
              "client_resources": dict(outer), **{key: None for key in counters},
              "flow_resources_available": False, "in_container_wall_seconds": None,
              "cpu_memory_scope": "UNAVAILABLE: no valid in-container flow meter; Docker client is diagnostic only",
              "wall_scope": "end-to-end including Docker launch/exit, not just OpenLane inner wall"}
    try:
        inner = json.loads((run / "container-resources.json").read_text())
        values = {key: inner[key] for key in (*counters, "wall_seconds")}
        if any(type(value) not in {int, float} or not math.isfinite(value) or value < 0
               for value in values.values()):
            raise ValueError("Invalid in-container resource counter")
        result.update({**{key: values[key] for key in counters}, "flow_resources_available": True,
                       "in_container_wall_seconds": inner["wall_seconds"],
                       "cpu_memory_scope": inner["scope"], "container_meter": inner})
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["flow_resource_error"] = str(error)
    return result


def cleanup(run: Path) -> None:
    cid = run / "container.id"
    if cid.is_file() and cid.read_text().strip():
        subprocess.run([DOCKER, "rm", "-f", cid.read_text().strip()],
                       capture_output=True, timeout=30, check=True)
    _LIVE.pop(str(run), None)
    _INSPECT_ERRORS.pop(str(run), None)
