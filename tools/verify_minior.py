"""Compare Minior core selection and edits with actual Mercury ROM routines."""

import argparse
import hashlib
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from box_data import BoxPokemon
from pokemon_data import (
    MINIOR_CORES,
    MINIOR_SPECIES,
    Pokemon,
    change_minior_color_pid,
    experience_for_level,
)
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    rng = random.Random(991)
    pids = [0, 1, 6, 7, 0xFFFFFFFF] + [rng.getrandbits(32) for _ in range(123)]
    for pid in pids:
        if engine.call(0x9D30C04, pid) != MINIOR_CORES[pid % 7]:
            raise ValueError("Minior core selector differs from PID % 7")
    template = trainer.snapshot()["party"][0]
    raw = bytearray(template.raw)
    struct.pack_into("<H", raw, 28, 0)
    normalizations = 0
    for species in MINIOR_SPECIES:
        for color in range(7):
            struct.pack_into("<I", raw, 0, color)
            struct.pack_into("<H", raw, 32, species)
            engine.write(0x2001000, bytes(raw))
            engine.call(0x9D30C5C, 0x2001000)
            actual = Pokemon(engine.read(0x2001000, 100))
            if actual.species != MINIOR_CORES[color] or actual.u16(28) != 0:
                raise ValueError(
                    "Actual form reversion did not normalize Minior to its PID core"
                )
            normalizations += 1
    for species, backup, target in [(160, 257, 257), (281, 160, 160), (991, 160, 1065)]:
        struct.pack_into("<I", raw, 0, 0)
        struct.pack_into("<H", raw, 28, backup)
        struct.pack_into("<H", raw, 32, species)
        engine.write(0x2001000, bytes(raw))
        engine.call(0x9D30C5C, 0x2001000)
        actual = Pokemon(engine.read(0x2001000, 100))
        expected_backup = backup if species == 991 else 0
        if actual.species != target or actual.u16(28) != expected_backup:
            raise ValueError(
                "Backup species field does not match the actual reversion path"
            )

    metadata = profile["species"][str(MINIOR_CORES[0])]
    raw = bytearray(template.raw)
    struct.pack_into("<H", raw, 28, 0)
    struct.pack_into("<H", raw, 32, MINIOR_CORES[0])
    struct.pack_into(
        "<I", raw, 0, change_minior_color_pid(template.pid, template.otid, 0)
    )
    struct.pack_into(
        "<I",
        raw,
        36,
        experience_for_level(
            template.level, metadata["growth"], profile["experience_tables"]
        ),
    )
    engine.write(0x2001000, bytes(raw))
    engine.call(0x803E47C, 0x2001000)
    mon = Pokemon(engine.read(0x2001000, 100))
    packed = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    pc_raw = bytearray(packed.raw)
    struct.pack_into("<H", pc_raw, 28, MINIOR_CORES[0])
    struct.pack_into(
        "<I", pc_raw, 0, change_minior_color_pid(packed.pid, packed.otid, 0)
    )
    struct.pack_into(
        "<I",
        pc_raw,
        32,
        experience_for_level(50, metadata["growth"], profile["experience_tables"]),
    )
    pc = BoxPokemon(bytes(pc_raw))
    edits = 0
    for color in range(7):
        for shiny in [False, True]:
            for nature in [0, 12, 24]:
                patches, _ = trainer.edit_pokemon(
                    {"party": [mon]}, 0, minior_color=color, nature=nature, shiny=shiny
                )
                updated = Pokemon(patches[0][2])
                engine.write(0x2001000, updated.raw)
                engine.call(0x9D30C5C, 0x2001000)
                actual = Pokemon(engine.read(0x2001000, 100))
                if (
                    actual.species != updated.species
                    or actual.pid != updated.pid
                    or actual.stats != updated.stats
                ):
                    raise ValueError(
                        "Edited party core or stats change during actual form reversion"
                    )
                if bool(engine.call(0x8044470, 0x2001000)) != shiny:
                    raise ValueError("Edited Minior shiny state differs from game")
                packed_updated, _ = pc.edit(
                    profile, minior_color=color, nature=nature, shiny=shiny
                )
                engine.write(0x2000800, packed_updated.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, 0x2001400)
                engine.call(0x9D30C5C, 0x2001400)
                actual_pc = Pokemon(engine.read(0x2001400, 100))
                if (
                    actual_pc.species != packed_updated.species
                    or actual_pc.pid != packed_updated.pid
                    or actual_pc.shiny != shiny
                    or actual_pc.pid % 25 != nature
                ):
                    raise ValueError(
                        "Edited PC Minior changes after ROM withdrawal and form reversion"
                    )
                edits += 2
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "pid_selections": len(pids),
        "form_normalizations": normalizations,
        "backup_species_cases": 3,
        "party_pc_edits": edits,
        "core_species": list(MINIOR_CORES),
        "scope": "Actual PID core selection, reversion, party stats/shiny and PC withdrawal conversion; not full battle transitions or live save/reload.",
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
