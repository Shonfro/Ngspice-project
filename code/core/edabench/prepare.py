"""Small preparation entry point, also useful for inspecting a deck before a run."""

import argparse
import json
from pathlib import Path

from . import hspice
from .inputs import json_write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--stop-ns", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config["tool"] != "hspice":
        parser.error("This preparation probe currently requires an HSPICE configuration")
    profile = next(p for p in config["profiles"] if p["name"] == args.profile)
    args.output.mkdir(parents=True, exist_ok=False)
    prepared = hspice.prepare(args.output, config, profile, args.stop_ns)
    json_write(args.output / "prepared.json", prepared)
    print(json.dumps(prepared, indent=2))


if __name__ == "__main__":
    main()
