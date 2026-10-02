"""Verify detail fields with real ROM getters/setters in isolated memory."""

import argparse
import hashlib
import json
import struct
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_rom_routines import RomCPU
from tools.read_state import read_state, StateMemory
from trainer_core import Trainer
from name_codec import encode_name


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    party = trainer.snapshot()["party"]
    cases = 0
    for mon in party:
        for name in ["A", "abc", "Abc1234"]:
            updated, _ = mon.edit(ot_name=name)
            engine.write(0x2001000, mon.raw)
            engine.write(0x2001400, encode_name(name, 8))
            engine.call(0x804037C, 0x2001000, 7, 0x2001400)
            if engine.read(0x2001000, 100) != updated.raw:
                raise ValueError("OT name write differs from actual SetMonData")
            engine.write(0x2001400, b"\0" * 8)
            engine.call(0x803FBE8, 0x2001000, 7, 0x2001400)
            if engine.read(0x2001400, 8) != encode_name(name, 7) + b"\xff":
                raise ValueError("OT name read differs from actual GetMonData")
            cases += 1
        for key, field, values in [
            ("friendship", 32, [0, 1, 127, 255]),
            ("met_location", 35, [0, 1, 143, 255]),
            ("met_level", 36, [0, 1, 100, 127]),
            ("ball", 38, [1, 3, 15, 26, 255]),
            ("ot_gender", 49, [0, 1]),
        ]:
            for value in values:
                updated, _ = mon.edit(**{key: value})
                engine.write(0x2001000, mon.raw)
                engine.write(0x2001400, struct.pack("<I", value))
                engine.call(0x804037C, 0x2001000, field, 0x2001400)
                if engine.read(0x2001000, 100) != updated.raw:
                    raise ValueError(f"{key} write differs from actual SetMonData")
                if engine.call(0x803FBE8, 0x2001000, field, 0x2001400) != value:
                    raise ValueError(f"{key} read differs from actual GetMonData")
                cases += 1
        for tid, sid in [(0, 0), (65535, 65535), (12345, 54321)]:
            updated, _ = mon.edit(ot_tid=tid, ot_sid=sid)
            engine.write(0x2001000, updated.raw)
            if engine.call(0x803FBE8, 0x2001000, 1, 0x2001400) != tid | sid << 16:
                raise ValueError("OT ID differs")
            if bool(engine.call(0x8044470, 0x2001000)) != mon.shiny:
                raise ValueError("OT ID edit changed shiny state")
            cases += 1
    snap = trainer.snapshot_trainer()
    for name in ["A", "abc", "Abc1234"]:
        engine.write(snap["address"], encode_name(name, 8))
        engine.write(0x2001400, b"\xfd\x01\xff")
        engine.call(0x8008FCC, 0x2001500, 0x2001400)
        if engine.read(0x2001500, len(name) + 1) != encode_name(name, len(name) + 1):
            raise ValueError("Player name differs from actual placeholder expansion")
        cases += 1
    for tid, sid in [(0, 0), (65535, 65535), (12345, 54321)]:
        engine.write(snap["address"] + 10, struct.pack("<HH", tid, sid))
        if engine.call(0x80CC1E4) != tid | sid << 16:
            raise ValueError("Player ID differs from GetCombinedOTId")
        cases += 1
    return {
        "passed": True,
        "detail_cases": cases,
        "rom_sha256": profile["rom_sha256"],
        "scope": "Isolated ROM getter/setter comparisons; not live save/reload or full legality.",
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
