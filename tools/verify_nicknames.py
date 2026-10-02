"""Verify ten-byte Pokemon nicknames against actual ROM getters/setters and PC unpack."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from name_codec import encode_name
from pokemon_data import Pokemon
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    names = ["A", "Abc1234567", "大力鳄小智", "小智Abc123"]
    party_cases = 0
    for mon in trainer.snapshot()["party"]:
        for name in names:
            encoded = encode_name(name, 10, maximum=10)
            updated, _ = mon.edit(nickname=name)
            engine.write(0x2001000, mon.raw)
            engine.write(0x2001800, encoded + b"\xff")
            engine.call(0x804037C, 0x2001000, 2, 0x2001800)
            if engine.read(0x2001000, 100) != updated.raw:
                raise ValueError("Nickname setter changes unexpected fields")
            engine.write(0x2001800, b"\x55" * 16)
            engine.call(0x803FBE8, 0x2001000, 2, 0x2001800)
            if engine.read(0x2001800, 11) != encoded + b"\xff":
                raise ValueError(
                    "Nickname getter lacks the expected appended terminator"
                )
            party_cases += 1
    records = [
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    ]
    if not records:
        raise ValueError("需要至少一个可验证PC成员")
    pc_cases = 0
    for index, mon in enumerate(records):
        for name in names if index == 0 else [names[index % len(names)]]:
            updated, _ = mon.edit(profile, nickname=name)
            engine.write(0x2000800, updated.raw)
            engine.call(0x9D54868, 0x2001000, 0x2000800)
            engine.call(0x803E774, 0x2001000, 0x2001400)
            actual = Pokemon(engine.read(0x2001400, 100))
            if actual.nickname != name or actual.raw[8:18] != updated.raw[8:18]:
                raise ValueError("PC nickname differs after actual unpack/withdrawal")
            if (
                actual.pid != mon.pid
                or actual.otid != mon.otid
                or actual.egg != mon.egg
            ):
                raise ValueError("PC nickname changes PID, OT ID or egg state")
            pc_cases += 1
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "party_get_set_cases": party_cases,
        "pc_records": len(records),
        "pc_nickname_transfers": pc_cases,
        "maximum_nickname_bytes": 10,
        "scope": "Actual party field-2 getter/setter and PC unpack/withdrawal; no automatic egg renaming, complete hatch animation or live save/reload.",
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
