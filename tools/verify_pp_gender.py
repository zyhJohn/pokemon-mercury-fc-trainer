"""Compare PP limits, packed PP Ups and gender edits with both supported ROMs.

Runs only an isolated CPU. The state is a fixture, not live-save migration proof.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from box_data import BoxPokemon
from pokemon_data import (
    Pokemon,
    gender,
    gender_choices,
    maximum_pp,
    experience_for_level,
)
from rom_versions import load_profile
from trainer_core import Trainer
from tools.read_state import read_state, StateMemory
from tools.verify_rom_routines import RomCPU


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    pp_cases = gender_boundaries = packed_cases = party_edits = pc_edits = refused = 0
    for move in profile["moves"]:
        for ups in range(4):
            for slot in range(4):
                if engine.call(
                    0x804101C, int(move), ups << (2 * slot), slot
                ) != maximum_pp(int(move), ups, profile["moves"]):
                    raise ValueError("Actual PP limit differs")
                pp_cases += 1
    for species, metadata in profile["species"].items():
        ratio = metadata["gender_ratio"]
        for low in sorted({0, 255, ratio & 255, max(0, ratio - 1)}):
            pid = 0x12345600 | low
            expected = {"雄性": 0, "雌性": 254, "无性别": 255}[gender(pid, ratio)]
            if engine.call(0x803F78C, int(species), pid) != expected:
                raise ValueError("Actual gender ratio differs")
            gender_boundaries += 1
    boxed = [
        m
        for i in range(25)
        for m in trainer.snapshot_box(i)["pokemon"]
        if m.species and not m.describe(profile)["errors"]
    ]
    if not boxed:
        raise ValueError("State needs at least one valid non-empty PC record")
    for bonus_byte in range(256):
        raw = bytearray(boxed[0].raw)
        raw[36] = bonus_byte
        engine.write(0x2001400, bytes(raw))
        engine.call(0x9D54868, 0x2001000, 0x2001400)
        unpacked = engine.read(0x2001000, 80)
        if unpacked[40] != bonus_byte:
            raise ValueError("Packed PP-Up mapping differs")
        for slot, move in enumerate(boxed[0].moves):
            if unpacked[52 + slot] != maximum_pp(
                move, (bonus_byte >> (2 * slot)) & 3, profile["moves"]
            ):
                raise ValueError("PC restored PP differs")
        packed_cases += 1
    template = trainer.snapshot()["party"][0]
    representatives = {1, 160, 201, 308, 1065, 1141, 1193}
    for ratio in {m["gender_ratio"] for m in profile["species"].values()}:
        representatives.add(
            next(
                int(s)
                for s, m in profile["species"].items()
                if m["gender_ratio"] == ratio
            )
        )
    for species in sorted(representatives):
        metadata = profile["species"][str(species)]
        for shiny in (False, True):
            raw = bytearray(template.raw)
            struct.pack_into("<H", raw, 28, 0)
            struct.pack_into("<H", raw, 32, species)
            struct.pack_into(
                "<I",
                raw,
                36,
                experience_for_level(
                    template.level, metadata["growth"], profile["experience_tables"]
                ),
            )
            if shiny:
                struct.pack_into(
                    "<I", raw, 4, (template.pid >> 16) ^ (template.pid & 65535)
                )
            raw[19] &= ~4
            raw[75] &= ~64
            engine.write(0x2001000, bytes(raw))
            engine.call(0x803E47C, 0x2001000)
            engine.write(0x2001000 + 86, engine.read(0x2001000 + 88, 2))
            source = Pokemon(engine.read(0x2001000, 100))
            for target in gender_choices(metadata["gender_ratio"]):
                changes = {"target_gender": target, "pp_ups": [3, 2, 1, 0]}
                if species == 308:
                    changes["spinda_seed"] = 123456
                patches, _ = trainer.edit_pokemon({"party": [source]}, 0, **changes)
                edited = Pokemon(patches[0][2])
                engine.write(0x2001000, edited.raw)
                actual = engine.call(0x803F720, 0x2001000)
                if actual != {"雄性": 0, "雌性": 254, "无性别": 255}[target]:
                    raise ValueError("Edited party gender differs")
                if (
                    edited.pid % 25 != source.pid % 25
                    or edited.shiny != source.shiny
                    or edited.ivs != source.ivs
                    or edited.evs != source.evs
                ):
                    raise ValueError("Gender edit changed unrelated constraints")
                expected_ability = trainer.validate_pokemon(edited)["ability_slot"]
                if (
                    engine.call(0x8040D38, 0x2001000)
                    != metadata["abilities"][expected_ability]
                ):
                    raise ValueError("Edited ability differs")
                engine.write(0x2001400, bytes([edited.raw[40]]))
                engine.write(0x2001000, source.raw)
                engine.call(0x804037C, 0x2001000, 21, 0x2001400)
                if engine.read(0x2001000 + 40, 1) != bytes([edited.raw[40]]):
                    raise ValueError("Actual PP-Up setter differs")
                party_edits += 1
    for source in boxed:
        ratio = profile["species"][str(source.species)]["gender_ratio"]
        current = gender(source.pid, ratio)
        target = next((g for g in gender_choices(ratio) if g != current), current)
        changes = {"target_gender": target, "pp_ups": [3, 2, 1, 0]}
        if source.species == 308:
            changes["spinda_seed"] = 123456
        try:
            edited, report = source.edit(profile, **changes)
        except ValueError as error:
            if "无法同时保留" not in str(error):
                raise
            refused += 1
            continue
        engine.write(0x2001400, edited.raw)
        engine.call(0x9D54868, 0x2001000, 0x2001400)
        engine.call(0x803E774, 0x2001000, 0x2001800)
        actual = Pokemon(engine.read(0x2001800, 100))
        if (
            engine.call(0x803F720, 0x2001800)
            != {"雄性": 0, "雌性": 254, "无性别": 255}[target]
            or actual.pp_ups != edited.pp_ups
            or any(
                actual.pp[i]
                != (
                    report["maximum_pp"][i] if move else engine.call(0x804101C, 0, 0, i)
                )
                for i, move in enumerate(edited.moves)
            )
            or actual.ivs != source.ivs
            or actual.evs != source.evs
            or actual.shiny != source.shiny
            or actual.egg != source.egg
        ):
            raise ValueError(
                f"PC withdrawal mismatch species {source.species}: "
                f"gender {engine.call(0x803F720, 0x2001800)} expected {target}; "
                f"PP {actual.pp}/{report['maximum_pp']}; ups {actual.pp_ups}/{edited.pp_ups}; "
                f"IV {actual.ivs == source.ivs}; EV {actual.evs == source.evs}; "
                f"shiny {actual.shiny}/{source.shiny}; egg {actual.egg}/{source.egg}"
            )
        pc_edits += 1
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "pp_function_cases": pp_cases,
        "gender_ratio_boundary_cases": gender_boundaries,
        "pc_bonus_unpack_cases": packed_cases,
        "party_edit_cases": party_edits,
        "pc_edit_cases": pc_edits,
        "unsatisfiable_constraints_refused": refused,
        "empty_slot_withdrawn_pp": engine.call(0x804101C, 0, 0, 0),
        "scope": "Isolated actual ROM; not live menu display, save/reload, full encounter legality or live-save migration",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    report = verify(rom, read_state(args.state), load_profile(rom))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
