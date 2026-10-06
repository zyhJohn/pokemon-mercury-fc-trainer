"""Read-only, release-pinned research of rival name and player avatar routines."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from name_codec import decode_name, encode_name
from rom_versions import RELEASES
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R5, UC_ARM_REG_SP


RIVAL_OFFSET = 0x3A4C
RIVAL_SIZE = 8
PLACEHOLDER_RIVAL = 6
TRAINER_TABLE = 0x0823EAC8
RIVAL_TRAINER_CLASSES = (0x51, 0x59, 0x5A)
FLASH_SECTION_4_SIZE = 0xD98


def thumb_bl_callers(rom, target):
    """Locate two-halfword Thumb BL calls to one exact address."""
    callers = []
    for offset in range(0, len(rom) - 3, 2):
        hi, lo = struct.unpack_from("<HH", rom, offset)
        if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
            continue
        displacement = ((hi & 0x7FF) << 11) | (lo & 0x7FF)
        if displacement & 0x200000:
            displacement -= 0x400000
        if 0x08000000 + offset + 4 + 2 * displacement == target:
            callers.append(0x08000000 + offset)
    return callers


def verify_battle_name_branch(engine, rom, rival_address):
    """Execute the actual trainer-class dispatch, stopping after name resolution."""
    if struct.unpack_from("<I", rom, 0xD8158)[0] != TRAINER_TABLE:
        raise ValueError("战斗文字使用的训练师表地址不同")
    if struct.unpack_from("<I", rom, 0x115230)[0] != TRAINER_TABLE:
        raise ValueError("第二处战斗名称使用的训练师表地址不同")
    matrix = []
    for trainer_id, expected_class in ((329, 0x51), (426, 0x59), (438, 0x5A), (1, None)):
        trainer_class = rom[TRAINER_TABLE - 0x08000000 + trainer_id * 40 + 1]
        if expected_class is not None and trainer_class != expected_class:
            raise ValueError("训练师表的劲敌类别与分支条件不匹配")
        engine.write(0x02001400, struct.pack("<H", trainer_id) + b"\0" * 6)
        engine.cpu.reg_write(UC_ARM_REG_R5, 0x02001400)
        engine.cpu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        engine.cpu.reg_write(UC_ARM_REG_LR, 0x02001101)
        stop = 0x080D8154 if trainer_class in RIVAL_TRAINER_CLASSES else 0x080D815C
        engine.cpu.emu_start(0x080D8135, stop, count=10000)
        if engine.cpu.reg_read(UC_ARM_REG_PC) != stop:
            raise ValueError(f"战斗名称分类分支没有到达预期出口：{engine.cpu.reg_read(UC_ARM_REG_PC):08X}")
        if trainer_class in RIVAL_TRAINER_CLASSES and engine.cpu.reg_read(UC_ARM_REG_R0) != rival_address:
            raise ValueError("战斗名称未读取 SaveBlock1 的劲敌字段")
        # A second trainer-introduction path applies the same three classes.
        engine.write(0x02001404, struct.pack("<H", trainer_id))
        engine.cpu.reg_write(UC_ARM_REG_R5, 0x02001400)
        engine.cpu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        engine.cpu.reg_write(UC_ARM_REG_LR, 0x02001101)
        second_stop = 0x08115220 if trainer_class in RIVAL_TRAINER_CLASSES else 0x08115234
        engine.cpu.emu_start(0x08115201, second_stop, count=10000)
        if engine.cpu.reg_read(UC_ARM_REG_PC) != second_stop:
            raise ValueError("第二处训练师名称分支没有到达预期出口")
        if trainer_class in RIVAL_TRAINER_CLASSES and engine.cpu.reg_read(UC_ARM_REG_R0) != rival_address:
            raise ValueError("第二处训练师名称未读取劲敌字段")
        matrix.append({"trainer_id": trainer_id, "class": trainer_class,
                       "uses_rival_field": trainer_class in RIVAL_TRAINER_CLASSES})
    return matrix


def verify_gender_cache(engine, rom, save2):
    avatar = struct.unpack_from("<I", rom, 0x5C96C)[0]
    if avatar != 0x02037078:
        raise ValueError("角色缓存地址与已验证的 ROM 常量不符")
    saved = engine.read(save2 + 8, 1)
    cached = engine.read(avatar + 7, 1)
    save_only = []
    cache_only = []
    try:
        for gender in (0, 1):
            engine.write(save2 + 8, bytes((gender,)))
            save_only.append([engine.call(0x0805C808, state) for state in range(8)])
        engine.write(save2 + 8, saved)
        for gender in (0, 1):
            engine.write(avatar + 7, bytes((gender,)))
            cache_only.append([engine.call(0x0805C808, state) for state in range(8)])
    finally:
        engine.write(save2 + 8, saved)
        engine.write(avatar + 7, cached)
    if save_only[0] != save_only[1]:
        raise ValueError("性别缓存的只读对照与预期不符")
    if cache_only[0] == cache_only[1]:
        raise ValueError("角色缓存性别未改变实际 ROM 的外观选择")
    return {
        "avatar_cache_address": avatar,
        "saveblock2_gender_offset": 8,
        "save_only_sprite_ids": save_only,
        "cache_only_sprite_ids": cache_only,
    }


def find_saved_rival_field(save, engine, address):
    """Find exact neighbourhoods in valid Flash section 4 copies, without editing."""
    if len(save) < 0x20000:
        raise ValueError("持久存档小于 GBA Flash 长度")
    neighbourhood = engine.read(address - 64, 8 + 128)
    matches = []
    for slot in range(2):
        base = slot * 0xE000
        for index in range(14):
            sector = save[base + index * 0x1000 : base + (index + 1) * 0x1000]
            section_id, expected_checksum, signature, counter = struct.unpack_from("<HHII", sector, 0xFF4)
            if section_id != 4 or signature != 0x08012025:
                continue
            total = sum(struct.unpack_from(f"<{FLASH_SECTION_4_SIZE // 4}I", sector)) & 0xFFFFFFFF
            checksum = ((total >> 16) + total) & 0xFFFF
            if checksum != expected_checksum:
                continue
            for offset in range(64, 0xF80 - 72):
                if sector[offset - 64 : offset + 72] == neighbourhood:
                    matches.append({"slot": slot, "sector_index": index,
                                    "section_id": section_id, "section_offset": offset,
                                    "save_counter": counter, "checksum_valid": True})
    return matches


def verify(rom, state, profile, save=None):
    sha = hashlib.sha256(rom).hexdigest()
    if sha != profile["rom_sha256"] or sha not in RELEASES:
        raise ValueError("ROM SHA-256 与受支持配置不匹配")
    engine = RomCPU(rom, state)
    save1 = struct.unpack("<I", engine.read(0x03005008, 4))[0]
    save2 = struct.unpack("<I", engine.read(0x0300500C, 4))[0]
    if not (0x02000000 <= save1 <= 0x0203C000 - RIVAL_OFFSET - RIVAL_SIZE):
        raise ValueError("SaveBlock1 指针超出 EWRAM")
    if not 0x02000000 <= save2 <= 0x0203FFF2:
        raise ValueError("SaveBlock2 指针超出 EWRAM")
    address = save1 + RIVAL_OFFSET
    original = engine.read(address, RIVAL_SIZE)
    original_name = decode_name(original)
    if original_name is None:
        raise ValueError("样本劲敌姓名包含未知编码，不能推断字段容量")
    if engine.call(0x080091E0, PLACEHOLDER_RIVAL) != address:
        raise ValueError("实际 ROM 占位符并未返回候选劲敌字段")
    battle_matrix = verify_battle_name_branch(engine, rom, address)
    gender_matrix = verify_gender_cache(engine, rom, save2)
    saved_locations = find_saved_rival_field(save, engine, address) if save is not None else None
    cases = []
    try:
        for name in ("A", "abc1234", "小智", "小智A12"):
            encoded = encode_name(name, RIVAL_SIZE)
            engine.write(address, encoded)
            pointer = engine.call(0x080091E0, PLACEHOLDER_RIVAL)
            if pointer != address:
                raise ValueError("占位符未跟随姓名字段")
            engine.write(0x02001400, b"\xfd\x06\xff")
            engine.write(0x02001500, b"\0" * 32)
            engine.call(0x08008FCC, 0x02001500, 0x02001400)
            length = encoded.index(0xFF) + 1
            actual = engine.read(0x02001500, length)
            if actual != encoded[:length]:
                raise ValueError("对白占位符展开与姓名字节不符")
            cases.append({"name": name, "bytes": length - 1})
    finally:
        engine.write(address, original)
    return {
        "rom_sha256": sha,
        "saveblock1_rival_offset": RIVAL_OFFSET,
        "rival_field_size": RIVAL_SIZE,
        "sample_decoded": original_name,
        "placeholder_routine": "080091E0",
        "dialogue_routine": "08008FCC",
        "placeholder_cases": cases,
        "placeholder_callers": [f"{x:08X}" for x in thumb_bl_callers(rom, 0x080091E0)],
        "battle_class_matrix": battle_matrix,
        "gender_matrix": gender_matrix,
        "persistent_sample_matches": saved_locations,
        "passed": True,
        "scope": "Isolated ROM routines and sample state: dialogue and two trainer-name class branches verified. Optional Flash match is observational; changed-name save/reload, other menus, sprite rebuild and gender-story coupling remain unverified.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES:
        raise ValueError("未知 ROM SHA-256")
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root / RELEASES[sha][2]).read_text(encoding="utf-8"))
    result = verify(rom, read_state(args.state), profile,
                    args.save.read_bytes() if args.save else None)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
