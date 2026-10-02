"""Verify synchronized held-item party/PC edits against actual ROM routines."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from box_data import BoxPokemon
from held_forms import HELD_FORM_FAMILIES, held_form_species
from pokemon_data import Pokemon, experience_for_level
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    # The instruction count remains bounded. Avoid one Windows timer thread per
    # call for this large matrix of already identified, short ROM routines.
    engine = RomCPU(rom, state, timeout_us=0)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    party_template = trainer.snapshot()["party"][0]
    pc_template = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    item_checks = 0
    for item, metadata in profile["items"].items():
        item = int(item)
        if (engine.call(0x9D3D570, item), engine.call(0x9D3D590, item)) != (
            metadata["hold_effect"],
            metadata["hold_parameter"],
        ):
            raise ValueError("Held effect/parameter differs from profile")
        item_checks += 2
    for item in range(750):
        if bool(engine.call(0x9D5B8FC, item)) != (
            item in profile["held_forms"]["special_z_crystals"]
        ):
            raise ValueError("Special Z crystal lookup differs")
    candidates = sorted(
        {
            0,
            13,
            *(
                int(item)
                for item, metadata in profile["items"].items()
                if metadata["hold_effect"] in (72, 86, 87, 88, 89, 90, 130)
            ),
        }
    )
    templates = {}
    edits = 0
    for family in HELD_FORM_FAMILIES:
        for species in family:
            metadata = profile["species"][str(species)]
            for slot, ability in enumerate(metadata["abilities"]):
                if not ability:
                    continue
                for held in candidates:
                    old_held = 0 if held == 13 else 13
                    key = (species, slot, old_held)
                    if key not in templates:
                        raw = bytearray(party_template.raw)
                        raw[19] &= ~4
                        pid = (party_template.pid & ~1) | (slot if slot < 2 else 0)
                        ivword = (party_template.u32(72) & 0x3FFFFFFF) | (
                            0x80000000 if slot == 2 else 0
                        )
                        struct.pack_into("<I", raw, 0, pid)
                        struct.pack_into("<H", raw, 28, 0)
                        struct.pack_into("<HH", raw, 32, species, old_held)
                        exp = (
                            experience_for_level(
                                party_template.level,
                                metadata["growth"],
                                profile["experience_tables"],
                            )
                            + 5
                        )
                        struct.pack_into("<I", raw, 36, exp)
                        struct.pack_into("<I", raw, 72, ivword)
                        engine.write(0x2001000, bytes(raw))
                        engine.call(0x803E47C, 0x2001000)
                        party = Pokemon(engine.read(0x2001000, 100))
                        raw = bytearray(pc_template.raw)
                        raw[19] &= ~4
                        struct.pack_into("<I", raw, 0, pid)
                        struct.pack_into("<HHI", raw, 28, species, old_held, exp)
                        struct.pack_into(
                            "<I",
                            raw,
                            54,
                            (pc_template.u32(54) & 0x3FFFFFFF)
                            | (0x80000000 if slot == 2 else 0),
                        )
                        templates[key] = party, BoxPokemon(bytes(raw))
                    party, pc = templates[key]
                    expected_species = held_form_species(
                        species, held, ability, profile
                    )
                    patches, _ = trainer.edit_pokemon({"party": [party]}, 0, held=held)
                    updated = Pokemon(patches[0][2])
                    raw = bytearray(party.raw)
                    struct.pack_into("<H", raw, 34, held)
                    engine.write(0x2001000, bytes(raw))
                    engine.call(0x9D31154, 0x2001000, held)
                    if (
                        engine.read(0x2001000, 100) != updated.raw
                        or updated.species != expected_species
                    ):
                        raise ValueError(
                            f"Party held-form edit differs: {species}/{held}/{slot}"
                        )
                    packed, report = pc.edit(profile, held=held)
                    if packed.experience != pc.experience or packed.pid != pc.pid:
                        raise ValueError("PC held form changed experience or PID")
                    if any(
                        packed.raw[index] != pc.raw[index]
                        for index in range(58)
                        if index not in range(28, 32)
                    ):
                        raise ValueError("PC held form changed unverified packed bytes")
                    engine.write(0x2000800, packed.raw)
                    engine.call(0x9D54868, 0x2001000, 0x2000800)
                    engine.call(0x803E774, 0x2001000, 0x2001400)
                    engine.call(0x9D31154, 0x2001400, held)
                    actual = Pokemon(engine.read(0x2001400, 100))
                    if (
                        actual.species,
                        actual.held,
                        actual.experience,
                        actual.pid,
                        actual.otid,
                    ) != (expected_species, held, pc.experience, pc.pid, pc.otid):
                        raise ValueError(
                            f"PC held-form edit differs: {species}/{held}/{slot}"
                        )
                    if actual.stats != report_stats(packed, report, profile):
                        raise ValueError(
                            "PC withdrawn stats differ from verified model"
                        )
                    if engine.call(0x8040D38, 0x2001400) != report["ability"]:
                        raise ValueError("PC held form ability differs")
                    if engine.call(0x803FBE8, 0x2001400, 12, 0) != held:
                        raise ValueError("PC held field differs from actual getter")
                    edits += 2
    egg_checks = 0
    for species, held in [
        (546, 490),
        (990, 507),
        (540, 489),
        (702, 524),
        (536, 487),
        (537, 488),
    ]:
        party, pc = templates[(species, 0, 13)]
        patches, _ = trainer.edit_pokemon({"party": [party]}, 0, egg=True)
        party_egg = Pokemon(patches[0][2])
        patches, _ = trainer.edit_pokemon({"party": [party_egg]}, 0, held=held)
        updated = Pokemon(patches[0][2])
        raw = bytearray(party_egg.raw)
        struct.pack_into("<H", raw, 34, held)
        engine.write(0x2001000, bytes(raw))
        engine.call(0x9D31154, 0x2001000, held)
        if (
            engine.read(0x2001000, 100) != updated.raw
            or not updated.egg
            or updated.level != 1
        ):
            raise ValueError("Party held form changed egg fields or level")
        pc_egg, _ = pc.edit(profile, egg=True)
        packed, _ = pc_egg.edit(profile, held=held)
        engine.write(0x2000800, packed.raw)
        engine.call(0x9D54868, 0x2001000, 0x2000800)
        engine.call(0x803E774, 0x2001000, 0x2001400)
        engine.call(0x9D31154, 0x2001400, held)
        actual = Pokemon(engine.read(0x2001400, 100))
        if (
            actual.species != packed.species
            or not actual.egg
            or actual.level != 1
            or actual.experience != packed.experience
        ):
            raise ValueError("PC held form differs after egg withdrawal conversion")
        egg_checks += 2
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "item_getter_checks": item_checks,
        "special_z_checks": 750,
        "party_pc_held_edits": edits,
        "family_species": sum(map(len, HELD_FORM_FAMILIES)),
        "egg_held_form_checks": egg_checks,
        "scope": "Actual item/form/ability routines, full party byte comparisons and PC unpack/withdrawal/stats; not complete encounter legality, live battle/item events or save/reload.",
    }


def report_stats(mon, report, profile):
    from pokemon_data import calculate_stats

    return calculate_stats(
        profile["species"][str(mon.species)]["base"],
        mon.ivs,
        mon.evs,
        report["level"],
        mon.pid % 25,
    )


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
