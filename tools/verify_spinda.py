"""Verify Spinda's current-ROM spot renderer and palette selection."""

import argparse
import hashlib
import json
import sys
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.read_state import read_state, StateMemory
from tools.verify_rom_routines import RomCPU
from spinda_images import read_spinda_assets, draw_spots
from pokemon_data import shiny_value, Pokemon, experience_for_level
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    assets = read_spinda_assets(StateMemory(state, rom), profile)
    patterns = {0, 0xFFFFFFFF, 0x12345678, 0x88888888}
    patterns.update(
        (0x88888888 & ~(255 << (8 * byte))) | (value << (8 * byte))
        for byte in range(4)
        for value in range(256)
    )
    cases = 0
    for tiles in [
        assets["tiles"],
        bytes([0x21, 0x43, 0x65, 0x87, 0xA9, 0xCB, 0xED, 0x0F]) * 256,
    ]:
        for pid in sorted(patterns):
            engine.write(0x2020000, b"\0" * 0x20000)
            engine.write(0x2020000, tiles)
            engine.call(0x8043458, 308, pid, 0x2020000, 1)
            if engine.read(0x2020000, 2048) != draw_spots(tiles, pid, assets["spots"]):
                raise ValueError(
                    f"Spinda pattern {pid:08X} differs from actual renderer"
                )
            cases += 1
    engine = RomCPU(
        rom, state
    )  # Restore state after the deliberately isolated image buffers.
    for species, front in [(307, 1), (308, 0)]:
        engine.write(0x2020000, assets["tiles"])
        engine.call(0x8043458, species, 0x12345678, 0x2020000, front)
        if engine.read(0x2020000, 2048) != assets["tiles"]:
            raise ValueError("Spot renderer affects another species or back sprite")
    for otid, pid in [(0, 0), (1, 0), (8, 0), (0xABCDEF01, 0x12345678)]:
        address = engine.call(0x80440F4, 308, otid, pid)
        key = "shiny_palette" if shiny_value(pid, otid) < 8 else "palette"
        if address != profile["spinda"][key]:
            raise ValueError("Spinda palette differs from actual selector")
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    raw = bytearray(trainer.snapshot()["party"][0].raw)
    struct.pack_into("<H", raw, 32, 308)
    struct.pack_into(
        "<I",
        raw,
        36,
        experience_for_level(
            raw[84], profile["species"]["308"]["growth"], profile["experience_tables"]
        ),
    )
    engine.write(0x2001000, bytes(raw))
    engine.call(0x803E47C, 0x2001000)
    raw = bytearray(engine.read(0x2001000, 100))
    struct.pack_into("<H", raw, 86, struct.unpack_from("<H", raw, 88)[0])
    mon = Pokemon(bytes(raw))
    edits = 0
    for nature in range(25):
        for shiny in [False, True]:
            patches, _ = trainer.edit_pokemon(
                {"party": [mon]}, 0, spinda_seed=0x12345678, nature=nature, shiny=shiny
            )
            updated = Pokemon(patches[0][2])
            engine.write(0x2001000, updated.raw)
            if bool(engine.call(0x8044470, 0x2001000)) != shiny:
                raise ValueError("Spinda shiny edit differs from game")
            abilities = profile["species"]["308"]["abilities"]
            slot = (
                2
                if updated.ability_flag and abilities[2]
                else (updated.pid & 1 if abilities[1] else 0)
            )
            if engine.call(0x8040D38, 0x2001000) != abilities[slot]:
                raise ValueError("Spinda ability differs from game")
            engine.call(0x803E47C, 0x2001000)
            if Pokemon(engine.read(0x2001000, 100)).stats != updated.stats:
                raise ValueError("Spinda stats differ from game")
            edits += 1
    return {
        "passed": True,
        "pattern_cases": cases,
        "edit_cases": edits,
        "rom_sha256": profile["rom_sha256"],
        "scope": "64x64 front sprite pixels and palette; not live rendering/save-reload or full PID legality.",
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
