"""Validate egg flags, stats and hatch detection in isolated actual ROM code."""

import argparse
import hashlib
import json
import struct
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_rom_routines import RomCPU
from tools.read_state import read_state, StateMemory
from trainer_core import Trainer, PARTY, PARTY_COUNT
from pokemon_data import Pokemon


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    snap = trainer.snapshot()
    checked = 0
    for slot, original in enumerate(snap["party"]):
        patch, _ = trainer.edit_pokemon(snap, slot, egg=True, ivs=[31] * 6)
        mon = Pokemon(patch[0][2])
        engine = RomCPU(rom, state)
        engine.write(0x2001000, mon.raw)
        if engine.call(0x803FBE8, 0x2001000, 45, 0x2001400) != 1:
            raise ValueError("Game does not recognize egg flag")
        if engine.call(0x803FBE8, 0x2001000, 6, 0x2001400) != 1:
            raise ValueError("Game does not recognize header egg flag")
        engine.call(0x803E47C, 0x2001000)
        if Pokemon(engine.read(0x2001000, 100)).stats != mon.stats:
            raise ValueError("Egg stats differ from game")
        engine.write(0x2001400, struct.pack("<I", 0))
        engine.call(0x804037C, 0x2001000, 45, 0x2001400)
        cleared = Pokemon(engine.read(0x2001000, 100))
        if cleared.egg or cleared.raw[19] & 4:
            raise ValueError("Game egg clear failed")
        # Detect a ready egg through the real overworld step routine.
        ready = bytearray(mon.raw)
        ready[41] = 1
        engine.write(PARTY, bytes(ready))
        engine.write(PARTY_COUNT, b"\1")
        detected = False
        for _ in range(1024):
            if engine.call(0x80463B8):
                detected = True
                break
        if not detected or engine.read(PARTY + 41, 1) != b"\0":
            raise ValueError("Egg cycle did not reach game hatch detection")
        checked += 1
    return {
        "passed": True,
        "egg_cases": checked,
        "scope": "ROM flag/stat/step hatch detection only; hatch animation, save/reload and encounter legality not verified.",
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
