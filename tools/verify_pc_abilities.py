"""Verify PC ability selection against actual ROM unpack/withdrawal and ability lookup."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from box_data import BoxPokemon
from pokemon_data import Pokemon, experience_for_level, gender, unown_form
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state, timeout_us=0)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    template = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    checks = species_count = pattern_changes = 0
    for species, metadata in profile["species"].items():
        species = int(species)
        raw = bytearray(template.raw)
        raw[19] &= ~4
        struct.pack_into(
            "<HHI",
            raw,
            28,
            species,
            0,
            experience_for_level(50, metadata["growth"], profile["experience_tables"]),
        )
        struct.pack_into("<I", raw, 54, template.u32(54) & 0x3FFFFFFF)
        source = BoxPokemon(bytes(raw))
        for slot, ability in enumerate(metadata["abilities"]):
            if not ability:
                continue
            changes = {"ability_slot": slot}
            if species == 308 and slot < 2 and slot != source.pid & 1:
                changes["spinda_seed"] = 0x12345678
                pattern_changes += 1
            updated, report = source.edit(profile, **changes)
            if (
                updated.shiny,
                updated.pid % 25,
                gender(updated.pid, metadata["gender_ratio"]),
            ) != (
                source.shiny,
                source.pid % 25,
                gender(source.pid, metadata["gender_ratio"]),
            ):
                raise ValueError("PC ability edit changed shiny/nature/gender")
            if (
                updated.ivs != source.ivs
                or updated.evs != source.evs
                or updated.egg != source.egg
            ):
                raise ValueError("PC ability edit changed IV/EV/egg")
            if species == 201 and unown_form(updated.pid) != unown_form(source.pid):
                raise ValueError("PC ability edit changed Unown letter")
            if any(
                updated.raw[index] != source.raw[index]
                for index in range(58)
                if index not in {*range(4), *range(54, 58)}
            ):
                raise ValueError("PC ability edit changed other compressed bytes")
            engine.write(0x2000800, updated.raw)
            engine.call(0x9D54868, 0x2001000, 0x2000800)
            engine.call(0x803E774, 0x2001000, 0x2001400)
            actual = Pokemon(engine.read(0x2001400, 100))
            if (
                actual.pid,
                actual.species,
                actual.ivs,
                actual.ability_flag,
                actual.egg,
            ) != (
                updated.pid,
                updated.species,
                updated.ivs,
                updated.ability_flag,
                updated.egg,
            ):
                raise ValueError("PC ability state differs after actual ROM withdrawal")
            if (
                engine.call(0x8040D38, 0x2001400) != ability
                or report["ability"] != ability
            ):
                raise ValueError(
                    f"Actual ability differs for species {species}, slot {slot}"
                )
            checks += 1
        species_count += 1
    single_party_checks = 0
    party_template = trainer.snapshot()["party"][0]
    for species in [201, 546, 990, 702, 303]:
        metadata = profile["species"][str(species)]
        raw = bytearray(party_template.raw)
        struct.pack_into("<I", raw, 0, party_template.pid | 1)
        struct.pack_into("<H", raw, 28, 0)
        struct.pack_into("<HH", raw, 32, species, 0)
        struct.pack_into(
            "<I",
            raw,
            36,
            experience_for_level(
                party_template.level, metadata["growth"], profile["experience_tables"]
            ),
        )
        engine.write(0x2001000, bytes(raw))
        engine.call(0x803E47C, 0x2001000)
        # Synthetic species swaps start with another species' current HP.
        # Use the actual freshly calculated maximum for this probe fixture.
        engine.write(0x2001000 + 86, engine.read(0x2001000 + 88, 2))
        source = Pokemon(engine.read(0x2001000, 100))
        patches, _ = trainer.edit_pokemon({"party": [source]}, 0, ability_slot=0)
        updated = Pokemon(patches[0][2])
        if updated.pid != source.pid:
            raise ValueError("Single ordinary ability unnecessarily changes PID")
        engine.write(0x2001000, updated.raw)
        if engine.call(0x8040D38, 0x2001000) != metadata["abilities"][0]:
            raise ValueError("Single party ability differs from actual ROM")
        single_party_checks += 1
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "species": species_count,
        "ability_selections": checks,
        "explicit_spinda_pattern_changes": pattern_changes,
        "single_party_ability_checks": single_party_checks,
        "scope": "Actual PC unpack/withdrawal and ability lookup with preserved nature/gender/shiny/IV/EV/egg, across available ROM slots; not full encounter legality or live save/reload.",
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
