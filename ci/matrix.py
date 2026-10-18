#!/usr/bin/env python3
"""Generate native package jobs from the collection's explicit platform inventory."""
import argparse
import json
from pathlib import Path

def matrix(family, selected="all"):
    inventory = json.loads((Path(__file__).resolve().parents[1] / "nix/platforms.json").read_text())
    runners = {"aarch64-darwin": "macos-15", "x86_64-linux": "ubuntu-24.04",
               "aarch64-linux": "ubuntu-24.04-arm"}
    wanted = [system for system in inventory if (system.endswith("-darwin")) == (family == "darwin")]
    if selected != "all" and not any(selected in inventory[system] for system in wanted):
        raise ValueError("Selected package is unavailable for this workflow family")
    jobs = [{"system": system, "runner": runners[system], "package": package}
            for system in wanted for package in inventory[system]
            if selected == "all" or package == selected]
    return {"include": jobs}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("family", choices=["darwin", "others"])
    parser.add_argument("--package", default="all")
    args = parser.parse_args()
    print(json.dumps(matrix(args.family, args.package), separators=(",", ":")))
