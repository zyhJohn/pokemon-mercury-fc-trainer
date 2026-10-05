"""PC egg conversion through actual unpack, withdrawal and hatch detection."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.read_state import read_state, StateMemory
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer, PARTY, PARTY_COUNT
from pokemon_data import Pokemon


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    conversions = 0
    detections = 0
    existing_eggs = 0
    allowed = {19, 37, 52} | set(range(32, 36)) | set(range(54, 58))
    engine = RomCPU(rom, state)
    for box in range(25):
        for original in trainer.snapshot_box(box)["pokemon"]:
            if not original.species:
                continue
            if original.egg:
                existing_eggs += 1
                continue
            egg, _ = original.edit(profile, egg=True, ivs=[31] * 6)
            for candidate in [egg, egg.edit(profile, egg=False)[0]]:
                if any(
                    original.raw[i] != candidate.raw[i]
                    for i in range(58)
                    if i not in allowed
                ):
                    raise ValueError("Egg conversion changes unrelated packed fields")
                engine.write(0x2000800, candidate.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, 0x2001400)
                actual = Pokemon(engine.read(0x2001400, 100))
                if (
                    actual.egg != candidate.egg
                    or bool(actual.raw[19] & 4) != candidate.egg
                ):
                    raise ValueError("PC egg flags differ after withdrawal")
                for field in [
                    "pid",
                    "otid",
                    "ivs",
                    "ability_flag",
                    "friendship",
                    "met_level",
                    "experience",
                ]:
                    if getattr(actual, field) != getattr(candidate, field):
                        raise ValueError(f"PC egg {field} differs after withdrawal")
                if actual.level != 1:
                    raise ValueError("Converted egg level differs")
                conversions += 1
            if detections < 6:
                engine = RomCPU(rom, state)
                ready, _ = egg.edit(profile, friendship=0)
                engine.write(0x2000800, ready.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, PARTY)
                engine.write(PARTY_COUNT, b"\1")
                detected = any(engine.call(0x80463B8) for _ in range(1024))
                if not detected or engine.read(PARTY + 41, 1) != b"\0":
                    raise ValueError(
                        "PC egg cannot reach hatch detection after withdrawal"
                    )
                detections += 1
                engine = RomCPU(rom, state)
    return {
        "passed": True,
        "conversion_cases": conversions,
        "hatch_detection_cases": detections,
        "zero_cycle_cases": detections,
        "existing_eggs_skipped": existing_eggs,
        "rom_sha256": profile["rom_sha256"],
        "scope": "Isolated ROM unpack/withdrawal and hatch detection; not full animation, save/reload or encounter legality.",
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
