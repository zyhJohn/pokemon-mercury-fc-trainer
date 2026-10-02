"""Compare compressed detail edits with actual ROM unpack and getters."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_rom_routines import RomCPU
from tools.read_state import read_state, StateMemory
from trainer_core import Trainer
from pokemon_data import Pokemon


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    cases = 0
    occupants = 0
    fields = [
        "pid",
        "otid",
        "ot_name",
        "friendship",
        "ball",
        "met_location",
        "met_level",
        "ot_gender",
        "shiny",
        "egg",
        "ability_flag",
    ]
    allowed = set(range(8)) | set(range(20, 27)) | {37, 38, 51, 52, 53}
    for box in range(25):
        for mon in trainer.snapshot_box(box)["pokemon"]:
            if not mon.species:
                continue
            occupants += 1
            variants = [
                dict(
                    friendship=255,
                    ball=26,
                    met_location=222,
                    met_level=127,
                    ot_gender=1,
                    ot_name="Abc1234",
                    ot_tid=12345,
                    ot_sid=54321,
                )
            ]
            if occupants == 1:
                variants += [
                    {key: value}
                    for key, values in [
                        ("friendship", [0, 1, 127, 255]),
                        ("ball", [0, 1, 15, 26, 255]),
                        ("met_location", [0, 1, 127, 255]),
                        ("met_level", [0, 1, 100, 127]),
                        ("ot_gender", [0, 1]),
                        ("ot_name", ["A", "abc", "Abc1234"]),
                    ]
                    for value in values
                ]
            for changes in variants:
                updated, _ = mon.edit(profile, **changes)
                if any(
                    mon.raw[i] != updated.raw[i] for i in range(58) if i not in allowed
                ):
                    raise ValueError("PC detail edit changed packed neighbor fields")
                engine.write(0x2000800, updated.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, 0x2001400)
                actual = Pokemon(engine.read(0x2001400, 100))
                for field in fields:
                    if getattr(updated, field) != getattr(actual, field):
                        raise ValueError(f"PC {field} differs from actual unpack")
                if updated.shiny != mon.shiny:
                    raise ValueError("OT ID edit changed shiny state")
                cases += 1
    return {
        "passed": True,
        "occupied_slots": occupants,
        "detail_cases": cases,
        "rom_sha256": profile["rom_sha256"],
        "scope": "Isolated ROM unpack/conversion; not live PC withdrawal, save/reload or full legality.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("rom_profile.json")
        .read_text(encoding="utf-8")
    )
    result = verify(args.rom.read_bytes(), read_state(args.state), profile)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
