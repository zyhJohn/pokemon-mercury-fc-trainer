"""Read-only audit of item-dependent forms; never enables an editor write path."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokemon_data import Pokemon, experience_for_level
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer

FAMILIES = ("阿尔宙斯", "银伴战兽", "骑拉帝纳", "盖诺赛克特", "帝牙卢卡", "帕路奇亚")


def audit(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    items = {}
    for ident in sorted({0, *map(int, profile["items"])}):
        effect = engine.call(0x9D3D570, ident)
        parameter = engine.call(0x9D3D590, ident)
        offset = profile["item_table_address"] - 0x8000000 + ident * 44
        if (effect, parameter) != tuple(rom[offset + 18 : offset + 20]):
            raise ValueError("Held effect/parameter differs from actual ROM getters")
        items[ident] = (effect, parameter)
    candidates = [
        ident
        for ident, (effect, _) in items.items()
        if effect in (72, 86, 87, 88, 89, 90, 130)
    ]
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    template = trainer.snapshot()["party"][0]
    names = {
        int(ident): metadata["name"]
        for ident, metadata in profile["species"].items()
        if any(
            metadata["name"] == family or metadata["name"].startswith(family + "（")
            for family in FAMILIES
        )
    }
    rows = []
    allowed = {32, 33, *range(86, 100)}
    for species, name in names.items():
        metadata = profile["species"][str(species)]
        # Include the same retained item on alternate forms and all available
        # ability slots. This still does not prove a PC write/persistence path.
        selected = sorted({0, 13, *candidates})
        for held, slot in (
            (held, slot)
            for held in selected
            for slot, ability in enumerate(metadata["abilities"])
            if ability
        ):
            raw = bytearray(template.raw)
            raw[19] &= ~4
            struct.pack_into(
                "<I",
                raw,
                72,
                (template.u32(72) & 0x3FFFFFFF) | (0x80000000 if slot == 2 else 0),
            )
            struct.pack_into(
                "<I", raw, 0, (template.pid & ~1) | (slot if slot < 2 else 0)
            )
            struct.pack_into("<H", raw, 28, 0)
            struct.pack_into("<HH", raw, 32, species, held)
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
            before = engine.read(0x2001000, 100)
            source_ability = engine.call(0x8040D38, 0x2001000)
            if source_ability != metadata["abilities"][slot]:
                raise ValueError("Source ability differs from the selected ROM slot")
            # Entry includes the initial movs r2,#0 at 0x09D31154.
            engine.call(0x9D31154, 0x2001000, held)
            after = engine.read(0x2001000, 100)
            if any(
                before[index] != after[index]
                for index in range(100)
                if index not in allowed
            ):
                raise ValueError(
                    "Held-form routine changes fields outside species/stats"
                )
            actual = Pokemon(after)
            if str(actual.species) not in profile["species"]:
                raise ValueError("Held-form routine produced an unknown species")
            rows.append(
                {
                    "source_species": species,
                    "item": held,
                    "effect": items[held][0],
                    "parameter": items[held][1],
                    "target_species": actual.species,
                    "ability_slot": slot,
                    "source_ability": source_ability,
                    "target_ability": engine.call(0x8040D38, 0x2001000),
                }
            )
    return {
        "audited": True,
        "rom_sha256": profile["rom_sha256"],
        "routine": "0x09D31154",
        "item_getter_checks": len(items) * 2,
        "family_species": names,
        "candidate_items": candidates,
        "cases": rows,
        "scope": "Read-only isolated-ROM probes across alternate forms, retained candidate items and all available ability slots. PC compression, persistence and live item changes remain unverified. This report does not enable new form writes.",
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
    result = audit(args.rom.read_bytes(), read_state(args.state), profile)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "audited": True,
                "item_getter_checks": result["item_getter_checks"],
                "species": len(result["family_species"]),
                "candidate_items": len(result["candidate_items"]),
                "form_probes": len(result["cases"]),
            }
        )
    )


if __name__ == "__main__":
    main()
