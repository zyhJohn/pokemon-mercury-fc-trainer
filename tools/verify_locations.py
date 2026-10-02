"""Verify ROM map labels and met-location field transfers in isolated memory."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unicorn import UC_ERR_READ_UNMAPPED, UcError

from name_codec import decode_name
from pokemon_data import Pokemon
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    # Suppress only the current-map department-store alias for section 94.
    # This flag is consulted for nullness; no object behind it is accessed.
    engine.write(0x20399D4, struct.pack("<I", 1))
    named = blank = 0
    for ident in range(256):
        if ident in profile["invalid_location_ids"]:
            pointer = struct.unpack_from(
                "<I", rom, profile["met_location_table"] - 0x8000000 + (ident - 88) * 4
            )[0]
            if pointer != 0xFFFFFFFF:
                raise ValueError("Invalid location pointer differs from the actual ROM")
            continue
        engine.write(0x2001800, b"\0" * 128)
        engine.call(0x80C4D78, 0x2001800, ident, 0)
        raw = engine.read(0x2001800, 128)
        if str(ident) in profile["met_locations"]:
            if decode_name(raw) != profile["met_locations"][str(ident)]:
                raise ValueError("Map label differs from actual GetMapName")
            named += 1
        else:
            if raw[:19] != b"\0" * 18 + b"\xff":
                raise ValueError(
                    "Out-of-range map ID differs from the actual blank-name fallback"
                )
            blank += 1
    # Confirm that this is an unsafe table entry rather than a decoded blank.
    try:
        engine.call(0x80C4D78, 0x2001800, 222, 0)
    except UcError as exc:
        if exc.errno != UC_ERR_READ_UNMAPPED:
            raise
    else:
        raise ValueError(
            "Invalid name pointer did not take the expected invalid-read path"
        )
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    mon = trainer.snapshot()["party"][0]
    pc = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    transfers = 0
    for ident in [0, 87, 88, 94, 143, 213, 221, 222, 252, 253, 255]:
        updated, _ = mon.edit(met_location=ident)
        engine.write(0x2001000, mon.raw)
        engine.write(0x2001800, struct.pack("<I", ident))
        engine.call(0x804037C, 0x2001000, 35, 0x2001800)
        if engine.read(0x2001000, 100) != updated.raw:
            raise ValueError("Met-location setter touches unexpected fields")
        if engine.call(0x803FBE8, 0x2001000, 35, 0x2001800) != ident:
            raise ValueError("Met-location getter differs")
        packed, _ = pc.edit(profile, met_location=ident)
        engine.write(0x2000800, packed.raw)
        engine.call(0x9D54868, 0x2001000, 0x2000800)
        engine.call(0x803E774, 0x2001000, 0x2001400)
        if Pokemon(engine.read(0x2001400, 100)).met_location != ident:
            raise ValueError("PC met-location differs after ROM unpack/withdrawal")
        transfers += 2
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "named_locations": named,
        "blank_fallbacks": blank,
        "invalid_pointers": len(profile["invalid_location_ids"]),
        "invalid_read_confirmed": True,
        "field_transfers": transfers,
        "scope": "Actual map-name lookup and field get/set, PC unpack/withdrawal; not encounter legality or live save/reload. Section 94 uses its static city label rather than the context-dependent department-store alias.",
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
