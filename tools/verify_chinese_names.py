"""Verify Mercury's Chinese glyph selection and name transfer in isolated RAM.

Requires the user's exact ROM/state and optional research dependencies. No game
font, save or character table is distributed; name IDs come from the stdlib codec.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unicorn.arm_const import (
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R4,
    UC_ARM_REG_R6,
    UC_ARM_REG_SP,
)

from name_codec import CHINESE_ENCODE, decode_name, encode_name
from pokemon_data import Pokemon
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def select_glyph(engine, pair, font, finish=False):
    engine.write(0x2001800, pair + b"\xff")
    engine.write(0x2001900, struct.pack("<I", 0x2001800) + b"\0" * 40)
    engine.write(0x2001A00, bytes((font, 0, 0, 0)))
    engine.cpu.reg_write(UC_ARM_REG_R4, 0x2001A00)
    engine.cpu.reg_write(UC_ARM_REG_R6, 0x2001900)
    engine.cpu.reg_write(UC_ARM_REG_SP, 0x3007E00)
    end = 0x8005BE0 if finish else 0x80068D4
    # The exact ROM is checked before execution; an instruction limit bounds
    # each run. Avoid creating a timeout thread for every glyph in the matrix.
    engine.cpu.emu_start(0x820F659, end, count=100000)
    if engine.cpu.reg_read(UC_ARM_REG_PC) != end:
        raise ValueError("Chinese renderer did not reach its expected continuation")
    if engine.read(0x2001900, 4) != struct.pack("<I", 0x2001802):
        raise ValueError("Chinese renderer did not consume exactly two bytes")


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    selections = 0
    rendered = 0
    for font, base in [(0, 0x8840000), (1, 0x87D0000)]:
        engine = RomCPU(rom, state)
        for pair in CHINESE_ENCODE.values():
            try:
                select_glyph(engine, pair, font)
            except Exception as exc:
                raise ValueError(
                    f"Glyph {pair.hex()} font {font}, PC {engine.cpu.reg_read(UC_ARM_REG_PC):08X}: {exc}"
                ) from exc
            lead, trail = pair
            row = lead - 1 - (lead > 6) - (lead > 27)
            expected_offset = ((row << 8) | trail) * 64
            if (
                engine.cpu.reg_read(UC_ARM_REG_R0) != base
                or engine.cpu.reg_read(UC_ARM_REG_R1) != expected_offset
            ):
                raise ValueError("Chinese glyph selection differs from actual ROM")
            if not any(
                rom[
                    base - 0x8000000 + expected_offset : base
                    - 0x8000000
                    + expected_offset
                    + 64
                ]
            ):
                raise ValueError("Chinese glyph is absent from the selected ROM font")
            selections += 1
        for char in "小智大力鳄妙蛙种子红明浩天水银":
            engine = RomCPU(rom, state)
            select_glyph(engine, CHINESE_ENCODE[char], font, finish=True)
            pixels = engine.read(0x3003DA0, 130)
            if pixels[128:] != bytes((10 if font == 0 else 12, 14)):
                raise ValueError(
                    "Chinese glyph width/height differs from verified font"
                )
            if len(set(pixels[:128])) < 2:
                raise ValueError("Chinese glyph decompressed to an empty image")
            rendered += 1

    # Restore RAM after renderer probes, then compare real name get/set paths.
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    known_species = {
        1: "妙蛙种子",
        2: "妙蛙草",
        3: "妙蛙花",
        4: "小火龙",
        5: "火恐龙",
        6: "喷火龙",
        25: "皮卡丘",
        160: "大力鳄",
        201: "未知图腾",
        308: "晃晃斑",
    }
    for species, name in known_species.items():
        engine.write(0x2001400, b"\0" * 16)
        engine.call(0x8040FD0, 0x2001400, species)
        if decode_name(engine.read(0x2001400, 11)) != name:
            raise ValueError("Chinese species name differs from the verified mapping")

    names = ["小智", "大力鳄", "大力鳄A", "小智A12", "Abc1234"]
    name_transfers = 0
    for mon in trainer.snapshot()["party"]:
        for name in names:
            updated, _ = mon.edit(ot_name=name)
            engine.write(0x2001000, mon.raw)
            engine.write(0x2001400, encode_name(name, 8))
            engine.call(0x804037C, 0x2001000, 7, 0x2001400)
            if engine.read(0x2001000, 100) != updated.raw:
                raise ValueError("Chinese OT name differs from SetMonData")
            engine.call(0x803FBE8, 0x2001000, 7, 0x2001400)
            if engine.read(0x2001400, 8) != encode_name(name, 7) + b"\xff":
                raise ValueError("Chinese OT name differs from GetMonData")
            name_transfers += 1
    packed = next(
        mon
        for box in range(25)
        for mon in trainer.snapshot_box(box)["pokemon"]
        if mon.species and not mon.describe(profile)["errors"]
    )
    for name in names:
        updated, _ = packed.edit(profile, ot_name=name)
        engine.write(0x2000800, updated.raw)
        engine.call(0x9D54868, 0x2001000, 0x2000800)
        engine.call(0x803E774, 0x2001000, 0x2001400)
        actual = Pokemon(engine.read(0x2001400, 100))
        if actual.ot_name != name or actual.raw[20:27] != updated.raw[20:27]:
            raise ValueError(
                "Chinese PC OT name differs after actual ROM withdrawal conversion"
            )
        name_transfers += 1
    player = trainer.snapshot_trainer()
    for name in names:
        encoded = encode_name(name, 8)
        engine.write(player["address"], encoded)
        engine.write(0x2001400, b"\xfd\x01\xff")
        engine.call(0x8008FCC, 0x2001500, 0x2001400)
        length = encoded.index(255) + 1
        if engine.read(0x2001500, length) != encoded[:length]:
            raise ValueError(
                "Chinese player name differs after actual placeholder expansion"
            )
        name_transfers += 1
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "chinese_characters": len(CHINESE_ENCODE),
        "glyph_selections": selections,
        "decompressed_glyphs": rendered,
        "known_species_names": len(known_species),
        "name_transfers": name_transfers,
        "scope": "Actual font selection/decompression, party getters/setters, PC withdrawal conversion and player placeholder expansion in isolated RAM; not live menu display or save/reload.",
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
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
