"""Reproduce supported-release checks in an isolated CPU; never write live RAM.

The state supplies test fixtures. It may come from an earlier release; this does
not prove that the new ROM has loaded that save successfully in mGBA.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rom_versions import load_profile
from tools.read_state import read_state
from tools import (
    verify_rom_routines,
    verify_economy,
    verify_time,
    verify_details,
    verify_icons_forms,
    verify_eggs,
    verify_pc_details,
    verify_pc_eggs,
    verify_spinda,
    verify_minior,
    verify_toxtricity,
    verify_locations,
    verify_nicknames,
    verify_held_forms,
    verify_pc_abilities,
    verify_chinese_names,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--all", action="store_true", help="Include all form, name and source matrices"
    )
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    profile = load_profile(rom)
    state = read_state(args.state)
    matrices = {
        "party_and_box_decode": lambda: verify_rom_routines.verify(
            rom, state, profile, True
        ),
        "box_edits": lambda: verify_rom_routines.verify_box_edits(rom, state, profile),
        "learnsets": lambda: verify_rom_routines.verify_learnsets(rom, state, profile),
        "machines": lambda: verify_rom_routines.verify_machines(rom, state, profile),
        "economy": lambda: verify_economy.verify(rom, state, profile),
        "time": lambda: verify_time.verify(rom, state, profile),
    }
    if args.all:
        for module in [
            verify_details,
            verify_icons_forms,
            verify_eggs,
            verify_pc_details,
            verify_pc_eggs,
            verify_spinda,
            verify_minior,
            verify_toxtricity,
            verify_locations,
            verify_nicknames,
            verify_held_forms,
            verify_pc_abilities,
            verify_chinese_names,
        ]:
            matrices[module.__name__.split(".")[-1]] = lambda m=module: m.verify(
                rom, state, profile
            )
    report = {
        "rom_sha256": profile["rom_sha256"],
        "state_sha256": hashlib.sha256(args.state.read_bytes()).hexdigest(),
        "profile": profile["name"],
        "scope": "Isolated actual ROM routines; state is a fixture, not proof of live save migration, menu display or save/reload.",
        "checks": {},
        "passed": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for name, run in matrices.items():
        print("Checking", name, flush=True)
        result = run()
        report["checks"][name] = result
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report["passed"] = True
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Release checks passed; private report:", args.report)


if __name__ == "__main__":
    main()
