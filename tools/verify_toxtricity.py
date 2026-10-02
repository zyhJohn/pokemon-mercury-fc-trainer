"""Verify nature-dependent Toxtricity forms with the actual ROM in isolated memory."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from box_data import BoxPokemon
from pokemon_data import (
    TOXTRICITY_HIGH_NATURES,
    TOXTRICITY_SPECIES,
    Pokemon,
    change_nature_pid,
    experience_for_level,
    toxtricity_species,
)
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    if profile["toxtricity"]["high_natures"] != list(TOXTRICITY_HIGH_NATURES):
        raise ValueError("ROM high-nature mask differs from the supported mapping")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    template = trainer.snapshot()["party"][0]
    checks = 0
    for species in [*TOXTRICITY_SPECIES, 160]:
        for nature in range(25):
            raw = bytearray(template.raw)
            struct.pack_into("<I", raw, 0, nature)
            struct.pack_into("<H", raw, 28, 0)
            struct.pack_into("<H", raw, 32, species)
            raw[19] &= ~4
            struct.pack_into("<I", raw, 72, template.u32(72) & ~0x40000000)
            engine.write(0x2001000, bytes(raw))
            actual_high = bool(engine.call(0x9D260F4, 0x2001000))
            if actual_high != (nature in TOXTRICITY_HIGH_NATURES):
                raise ValueError("Actual high-nature selector differs")
            engine.call(0x9D310F4, 0x2001000)
            expected = (
                toxtricity_species(nature) if species in TOXTRICITY_SPECIES else species
            )
            struct.pack_into("<H", raw, 32, expected)
            if engine.read(0x2001000, 100) != bytes(raw):
                raise ValueError("Actual form setter changes unexpected fields")
            checks += 1
    packed = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    edits = 0
    egg_checks = 0
    for species, initial_nature in [(1141, 0), (1193, 1)]:
        metadata = profile["species"][str(species)]
        raw = bytearray(template.raw)
        struct.pack_into("<H", raw, 28, 0)
        struct.pack_into("<H", raw, 32, species)
        struct.pack_into(
            "<I",
            raw,
            0,
            change_nature_pid(template.pid, template.otid, initial_nature, species),
        )
        raw[19] &= ~4
        struct.pack_into("<I", raw, 72, template.u32(72) & ~0x40000000)
        struct.pack_into(
            "<I",
            raw,
            36,
            experience_for_level(
                template.level, metadata["growth"], profile["experience_tables"]
            )
            + 5,
        )
        engine.write(0x2001000, bytes(raw))
        engine.call(0x803E47C, 0x2001000)
        party = Pokemon(engine.read(0x2001000, 100))
        raw = bytearray(packed.raw)
        struct.pack_into("<H", raw, 28, species)
        struct.pack_into(
            "<I",
            raw,
            0,
            change_nature_pid(packed.pid, packed.otid, initial_nature, species),
        )
        raw[19] &= ~4
        struct.pack_into("<I", raw, 54, packed.u32(54) & ~0x40000000)
        struct.pack_into(
            "<I",
            raw,
            32,
            experience_for_level(50, metadata["growth"], profile["experience_tables"])
            + 5,
        )
        pc = BoxPokemon(bytes(raw))
        for nature in [0, 1]:
            patches, _ = trainer.edit_pokemon(
                {"party": [party]}, 0, nature=nature, egg=True
            )
            egg = Pokemon(patches[0][2])
            pc_egg, _ = pc.edit(profile, nature=nature, egg=True)
            for record in [egg, pc_egg]:
                if isinstance(record, BoxPokemon):
                    engine.write(0x2000800, record.raw)
                    engine.call(0x9D54868, 0x2001000, 0x2000800)
                    engine.call(0x803E774, 0x2001000, 0x2001400)
                    actual_egg = Pokemon(engine.read(0x2001400, 100))
                else:
                    actual_egg = record
                engine.write(0x2001400, actual_egg.raw)
                engine.call(0x9D310F4, 0x2001400)
                normalized = Pokemon(engine.read(0x2001400, 100))
                if (
                    normalized.raw != actual_egg.raw
                    or not normalized.egg
                    or normalized.level != 1
                    or normalized.species != toxtricity_species(nature)
                ):
                    raise ValueError(
                        "Edited egg form differs after actual unpack or form check"
                    )
                egg_checks += 1
        for nature in range(25):
            for shiny in [False, True]:
                patches, _ = trainer.edit_pokemon(
                    {"party": [party]}, 0, nature=nature, shiny=shiny
                )
                updated = Pokemon(patches[0][2])
                engine.write(0x2001000, updated.raw)
                engine.call(0x9D310F4, 0x2001000)
                if engine.read(0x2001000, 100) != updated.raw:
                    raise ValueError(
                        "Edited party form is changed by actual normalization"
                    )
                engine.call(0x803E47C, 0x2001000)
                actual = Pokemon(engine.read(0x2001000, 100))
                if (
                    actual.stats != updated.stats
                    or updated.experience != party.experience
                ):
                    raise ValueError(
                        "Edited party stats or preserved experience differs"
                    )
                if bool(engine.call(0x8044470, 0x2001000)) != shiny:
                    raise ValueError("Actual party shiny state differs")
                packed_updated, report = pc.edit(profile, nature=nature, shiny=shiny)
                engine.write(0x2000800, packed_updated.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, 0x2001400)
                engine.call(0x9D310F4, 0x2001400)
                actual_pc = Pokemon(engine.read(0x2001400, 100))
                if (
                    actual_pc.species,
                    actual_pc.pid,
                    actual_pc.shiny,
                    actual_pc.experience,
                ) != (packed_updated.species, packed_updated.pid, shiny, pc.experience):
                    raise ValueError(
                        "Edited PC form or PID differs after actual withdrawal"
                    )
                ability = engine.call(0x8040D38, 0x2001400)
                if ability != report["ability"]:
                    raise ValueError(
                        "Actual PC ability differs after nature/form change"
                    )
                edits += 2
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "nature_form_checks": checks,
        "party_pc_edits": edits,
        "egg_form_checks": egg_checks,
        "high_natures": list(TOXTRICITY_HIGH_NATURES),
        "scope": "Actual nature selector, form setter, party stats/shiny and PC unpack/withdrawal/ability; not full battle transitions, hatching or live save/reload.",
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
