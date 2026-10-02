"""Run actual ROM routines in an isolated ARM CPU, never a running mGBA process.

Optional research dependency: unicorn==2.1.4. This checks algorithm agreement,
not emulation, Lua integration, persistence, encounter legality or gameplay.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
from unicorn.arm_const import (
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
)
from tools.read_state import read_state, StateMemory
from pokemon_data import Pokemon, calculate_stats
from trainer_core import Trainer


class RomCPU:
    def __init__(self, rom, state):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for a, size, data in [
            (0x8000000, len(rom), rom),
            (0x2000000, 0x40000, state[0x21000:]),
            (0x3000000, 0x8000, state[0x19000:0x21000]),
        ]:
            self.cpu.mem_map(a, size)
            self.cpu.mem_write(a, data)

    def call(self, address, *args):
        for register, value in zip(
            [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3], args
        ):
            self.cpu.reg_write(register, value)
        self.cpu.reg_write(UC_ARM_REG_SP, 0x3007E00)
        self.cpu.reg_write(UC_ARM_REG_LR, 0x2001101)
        self.cpu.emu_start(address | 1, 0x2001100, timeout=200000, count=1000000)
        if self.cpu.reg_read(UC_ARM_REG_PC) != 0x2001100:
            raise ValueError("ROM routine did not return within its limit")
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def write(self, address, data):
        self.cpu.mem_write(address, data)

    def read(self, address, n):
        return bytes(self.cpu.mem_read(address, n))


def verify_learnsets(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    count = 0
    for ident, entries in profile["level_up_learnsets"].items():
        size = engine.call(0x9D41AF8, int(ident), 0x2001200)
        actual = [
            list(pair)
            for pair in struct.iter_unpack("<HH", engine.read(0x2001200, size * 4))
        ]
        if actual != entries[:50]:
            raise ValueError(f"物种 {ident}：等级学习表与 ROM 函数不同")
        count += 1
    return {
        "rom_sha256": profile["rom_sha256"],
        "level_up_tables": count,
        "passed": True,
        "scope": "ROM level-up table only; TM, tutor, egg, pre-evolution and event sources not checked.",
    }


def verify_machines(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    mapped = 0
    compatibility = 0
    for ident, item in profile["items"].items():
        if "tm_index" not in item:
            continue
        if engine.call(0x9D3D852, int(ident)) != item["tm_index"]:
            raise ValueError(f"道具 {ident} 的学习器索引不同")
        if engine.call(0x8125A78, int(ident)) != item["move"]:
            raise ValueError(f"道具 {ident} 的学习器招式不同")
        mapped += 1
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    snap = trainer.snapshot()
    for mon in snap["party"]:
        engine.write(0x2001000, mon.raw)
        for index in range(128):
            expected = (
                index in profile["tm_compatibility"][str(mon.species)] and not mon.egg
            )
            if bool(engine.call(0x8043C2C, 0x2001000, index)) != expected:
                raise ValueError(f"物种 {mon.species} 学习器 {index} 兼容性不同")
            compatibility += 1
    if snap["party"]:
        raw = bytearray(snap["party"][0].raw)
        raw[19] &= ~4
        raw[75] &= ~64
        for ident, indices in profile["tm_compatibility"].items():
            struct.pack_into("<H", raw, 32, int(ident))
            engine.write(0x2001000, bytes(raw))
            for index in [0, 31, 32, 63, 64, 95, 96, 127]:
                if bool(engine.call(0x8043C2C, 0x2001000, index)) != (index in indices):
                    raise ValueError(f"物种 {ident} 学习器 {index} 边界位不同")
                compatibility += 1
    return {
        "rom_sha256": profile["rom_sha256"],
        "machine_items": mapped,
        "compatibility_cases": compatibility,
        "passed": True,
        "scope": "ROM TM/HM mapping and compatibility; availability and story requirements not checked.",
    }


def verify_box_edits(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    cases = 0
    refused = 0
    for box_index in range(25):
        snapshot = trainer.snapshot_box(box_index)
        for mon in snapshot["pokemon"]:
            if not mon.species:
                continue
            for changes in [
                dict(ivs=[31] * 6, evs=[252, 0, 0, 252, 0, 6]),
                dict(shiny=not mon.shiny),
                dict(nature=(mon.pid % 25 + 1) % 25),
            ]:
                try:
                    updated, info = mon.edit(profile, **changes)
                except ValueError as exc:
                    if (
                        "PID 花纹" in str(exc)
                        or "未找到保持" in str(exc)
                        or "无法保留" in str(exc)
                    ):
                        refused += 1
                        continue
                    raise
                allowed = set(range(4)) | set(range(44, 50)) | set(range(54, 58))
                if any(
                    mon.raw[i] != updated.raw[i] for i in range(58) if i not in allowed
                ):
                    raise ValueError("盒内编辑更改了无关压缩字段")
                engine.write(0x2000800, updated.raw)
                engine.call(0x9D54868, 0x2001000, 0x2000800)
                engine.call(0x803E774, 0x2001000, 0x2001400)
                actual = Pokemon(engine.read(0x2001400, 100))
                for field in [
                    "species",
                    "pid",
                    "otid",
                    "held",
                    "experience",
                    "moves",
                    "ivs",
                    "evs",
                    "egg",
                    "ability_flag",
                    "shiny",
                ]:
                    if getattr(actual, field) != getattr(updated, field):
                        raise ValueError(f"盒内修改后的 {field} 与游戏解码不同")
                expected = calculate_stats(
                    profile["species"][str(mon.species)]["base"],
                    updated.ivs,
                    updated.evs,
                    info["level"],
                    updated.pid % 25,
                    mon.species == 303,
                )
                if actual.stats != expected or actual.level != info["level"]:
                    raise ValueError("盒内修改后游戏取出计算的等级或能力值不同")
                cases += 1
    return {
        "rom_sha256": profile["rom_sha256"],
        "box_edit_cases": cases,
        "unsupported_refused": refused,
        "passed": True,
        "scope": "Edited packed bytes decoded and converted to party records by actual ROM routines; not live mGBA persistence.",
    }


def verify(rom, state, profile, all_species=False):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    counts = {
        "box_decode": 0,
        "party_stat_recalculation": 0,
        "shiny": 0,
        "ability": 0,
        "all_species": 0,
    }
    for box_index in range(len(profile["storage"]["box_addresses"])):
        snap = trainer.snapshot_box(box_index)
        for i, box in enumerate(snap["pokemon"]):
            if not box.species:
                continue
            engine.call(0x9D54868, 0x2001000, snap["address"] + i * 58)
            mon = Pokemon(engine.read(0x2001000, 80) + b"\0" * 20)
            for field in [
                "species",
                "pid",
                "otid",
                "held",
                "experience",
                "moves",
                "ivs",
                "evs",
                "egg",
                "ability_flag",
                "shiny",
            ]:
                if getattr(box, field) != getattr(mon, field):
                    raise ValueError(
                        f"盒 {box_index + 1} 格 {i + 1}：{field} 与 ROM 解码结果不同"
                    )
            counts["box_decode"] += 1
    snap = trainer.snapshot()
    for slot, original in enumerate(snap["party"]):
        metadata = profile["species"][str(original.species)]
        variants = [original]
        for nature in range(25):
            variants.append(original.edit(nature=nature, base=metadata["base"])[0])
        for shiny in [False, True]:
            for level in [1, 50, 100]:
                for ability_slot, a in enumerate(metadata["abilities"]):
                    if not a:
                        continue
                    mon, _ = original.edit(
                        shiny=shiny,
                        level=level,
                        growth=metadata["growth"],
                        experience_tables=profile["experience_tables"],
                        ivs=[31, 0, 15, 30, 1, 29],
                        evs=[252, 0, 4, 252, 0, 2],
                        base=metadata["base"],
                        ability_slot=ability_slot,
                        abilities=metadata["abilities"],
                        gender_ratio=metadata["gender_ratio"],
                    )
                    variants.append(mon)
        for mon in variants:
            engine.write(0x2001000, mon.raw)
            engine.call(0x803E47C, 0x2001000)
            actual = Pokemon(engine.read(0x2001000, 100))
            if actual.stats != mon.stats or actual.hp != mon.hp:
                raise ValueError(
                    f"队伍 {slot + 1}：能力/HP 重算不同 {actual.stats} / {mon.stats}, HP {actual.hp}/{mon.hp}"
                )
            counts["party_stat_recalculation"] += 1
            if bool(engine.call(0x8044470, 0x2001000)) != mon.shiny:
                raise ValueError(f"队伍 {slot + 1}：闪光判定不同")
            counts["shiny"] += 1
            abilities = metadata["abilities"]
            index = (
                2
                if mon.ability_flag and abilities[2]
                else (mon.pid & 1 if abilities[1] else 0)
            )
            if engine.call(0x8040D38, 0x2001000) != abilities[index]:
                raise ValueError(f"队伍 {slot + 1}：特性判定不同")
            counts["ability"] += 1
    if all_species and snap["party"]:
        original = snap["party"][0]
        for ident, metadata in profile["species"].items():
            mon, _ = original.edit(
                species=int(ident),
                level=50,
                growth=metadata["growth"],
                experience_tables=profile["experience_tables"],
                ivs=[0, 31, 15, 1, 30, 29],
                evs=[0, 252, 6, 252, 0, 0],
                base=metadata["base"],
            )
            engine.write(0x2001000, mon.raw)
            engine.call(0x803E47C, 0x2001000)
            actual = Pokemon(engine.read(0x2001000, 100))
            if actual.stats != mon.stats or actual.level != mon.level:
                raise ValueError(
                    f"物种 {ident}：能力值或等级与 ROM 不同 {actual.stats} / {mon.stats}"
                )
            for slot, a in enumerate(metadata["abilities"]):
                if not a:
                    continue
                data = bytearray(mon.raw)
                struct.pack_into(
                    "<I", data, 0, (mon.pid & ~1) | (slot if slot < 2 else 0)
                )
                data[75] = (data[75] | 128) if slot == 2 else (data[75] & 127)
                engine.write(0x2001000, bytes(data))
                found = engine.call(0x8040D38, 0x2001000)
                if found != a:
                    raise ValueError(
                        f"物种 {ident} 特性槽 {slot}：ROM {found} != 表 {a}"
                    )
            counts["all_species"] += 1
    return {
        "rom_sha256": profile["rom_sha256"],
        "checks": counts,
        "passed": True,
        "scope": "Isolated execution of actual ROM routines; not a live mGBA write test.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--all-species", action="store_true")
    parser.add_argument("--learnsets-only", action="store_true")
    parser.add_argument("--machines-only", action="store_true")
    parser.add_argument("--box-edits-only", action="store_true")
    args = parser.parse_args()
    profile = json.loads(
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("rom_profile.json")
        .read_text(encoding="utf-8")
    )
    if args.box_edits_only:
        result = verify_box_edits(
            args.rom.read_bytes(), read_state(args.state), profile
        )
    elif args.machines_only:
        result = verify_machines(args.rom.read_bytes(), read_state(args.state), profile)
    elif args.learnsets_only:
        result = verify_learnsets(
            args.rom.read_bytes(), read_state(args.state), profile
        )
    else:
        result = verify(
            args.rom.read_bytes(), read_state(args.state), profile, args.all_species
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
