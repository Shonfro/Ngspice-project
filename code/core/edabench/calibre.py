"""Calibre adapter: full hierarchical DRC, not a SPICE transient or help test."""

from pathlib import Path
import re
import shutil

from .inputs import sha256

TOOL = "/opt/eda_public/bin/calibre"
BINARY = "/opt/vendor/siemens/calibre/2021/aoi_cal_2021.2_28.15/bin/calibre"
PROCESS_NAMES = {"calibre", "calibredrv", "calibre_drc"}


def prepare(run: Path, config: dict, profile: dict, stop_ns: None) -> dict:
    source = Path(config["source_directory"]).resolve(strict=True)
    files = []
    for path in source.rglob("*"):
        if path.is_file():
            relative = path.relative_to(source)
            target = run / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            files.append({"path": str(path), "sha256": sha256(path)})
    return {"command": [TOOL, *profile["cli"], config["deck"]],
            "inputs": {"files": files}, "output_contract": "Hierarchical DRC database and summary"}


def validate(run: Path, config: dict, stop_ns: None, reference: Path | None = None) -> dict:
    summary = run / config.get("summary", "drc.summary")
    if not summary.is_file():
        return {"passed": False, "reason": "missing completed DRC summary"}
    text = summary.read_text()
    found = re.search(r"TOTAL DRC Results Generated:\s+(\d+)(?:\s+\((\d+)\))?", text)
    counts = [int(found[1]), int(found[2] or found[1])] if found else None
    expected = config.get("expected_result_counts")
    passed = bool(found) and (expected is None or counts == expected)
    if reference:
        previous = validate(reference, config, None)
        passed &= counts == previous.get("result_counts")
    return {"passed": passed, "result_counts": counts, "expected_result_counts": expected,
            "scope": "This public smoke deck is not foundry signoff or customer workload"}
