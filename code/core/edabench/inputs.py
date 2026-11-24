"""Stage an immutable SPICE include closure without editing shared inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def spice_closure(source: Path) -> dict[Path, str]:
    """Resolve literal .include/.lib files; reject unresolved dependencies."""
    pending = [source.resolve(strict=True)]
    texts: dict[Path, str] = {}
    while pending:
        path = pending.pop()
        if path in texts:
            continue
        text = path.read_bytes().decode("latin1")
        texts[path] = text
        for line in text.splitlines():
            match = re.match(r"\s*\.(include|inc|lib)\s+(.+)", line, re.I)
            if not match:
                continue
            tokens = shlex.split(match[2], posix=True)
            if not tokens:
                raise ValueError(f"Empty include directive: {path}: {line}")
            if match[1].lower() == "lib" and len(tokens) == 1:
                continue  # .lib <section>, not an external library
            child = (path.parent / tokens[0]).resolve(strict=True)
            pending.append(child)
    return texts


def stage_spice(source: Path, output: Path, stop_ns: float) -> dict:
    texts = spice_closure(source)
    names = {path: f"{sha256(path)[:16]}-{path.name}" for path in texts}
    dependencies = output / "models"
    dependencies.mkdir()
    source = source.resolve()
    transformations = []
    for path, original in texts.items():
        def include(match: re.Match) -> str:
            tokens = shlex.split(match[2], posix=True)
            if match[1].lower() == "lib" and len(tokens) == 1:
                return match[0]
            child = (path.parent / tokens[0]).resolve(strict=True)
            prefix = "models/" if path == source else ""
            suffix = " " + " ".join(tokens[1:]) if len(tokens) > 1 else ""
            return f'.{match[1]} "{prefix}{names[child]}"{suffix}'

        adapted = re.sub(r"^\s*\.(include|inc|lib)\s+([^\r\n]+)", include,
                         original, flags=re.I | re.M)
        if path == source:
            pattern = r"^(\s*\.tran\s+\S+\s+)(\S+)"
            changes = list(re.finditer(pattern, adapted, flags=re.I | re.M))
            if len(changes) != 1:
                raise ValueError("Exactly one top-level .tran is required")
            transformations.append({"directive": "tran_stop", "original": changes[0][2],
                                    "new": f"{stop_ns:g}ns"})
            adapted = re.sub(pattern, lambda m: m[1] + f"{stop_ns:g}ns", adapted,
                             count=1, flags=re.I | re.M)
            target = output / "circuit.sp"
        else:
            target = dependencies / names[path]
        target.write_bytes(adapted.encode("latin1"))
    manifest = {
        "source": str(source),
        "closure": [{"path": str(path), "sha256": sha256(path),
                     "staged": "circuit.sp" if path == source else f"models/{names[path]}"}
                    for path in sorted(texts)],
        "transformations": ["resolve literal include paths", *transformations],
        "circuit_model_stimulus_tolerances_unchanged": True,
    }
    json_write(output / "inputs.json", manifest)
    return manifest
