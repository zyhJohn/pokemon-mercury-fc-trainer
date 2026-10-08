"""Trace the actual map-object avatar initializer in exact Mercury ROMs.

ROM, mGBA state and optional Flash save are read only. All mutations are made
in a fresh isolated Unicorn instance for each matrix row.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import load_profile, release
from tools.audit_player_avatar_v2 import IsolatedROM
from tools.read_state import read_state

ENTRY = {
    "1.0": (0x09D0D461, 0x09D0D428, 0x09D0D3CC),
    "1.2": (0x09D0F8E5, 0x09D0F8AC, 0x09D0F850),
}
AVATAR = 0x02037078
OBJECTS = 0x02036E38
OBJECT_BYTES = 0x24
GENDER_SPECIAL = 0x0806C4F0
RIVAL_TRAINER_TABLE = 0x0823EAC8


def bl_target(rom, address):
    """Decode one ROM Thumb BL to guard each exact map-init edge."""
    hi, lo = struct.unpack_from("<HH", rom, address - 0x08000000)
    if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
        raise ValueError(f"不是预期 Thumb BL：{address:08X}")
    displacement = ((hi & 0x7FF) << 11) | (lo & 0x7FF)
    if displacement & 0x200000:
        displacement -= 0x400000
    return address + 4 + 2 * displacement


def check_static(rom, version):
    init, _, reverse_gender = ENTRY[version]
    for source, target in (
        (0x08057106, 0x0805EDF0),  # load map object events
        (0x0805EE20, 0x0805EE3C),  # spawn each object
        (0x0805EF60, 0x0805F02C),  # player movement type 0x0B
    ):
        if bl_target(rom, source) != target:
            raise ValueError(f"地图对象初始化调用已变化：{source:08X}")
    if rom[0x5EF56:0x5EF5C] != bytes.fromhex("B0 79 0B 28 06 D1"):
        raise ValueError("玩家对象移动类型分支已变化")
    if rom[0x5F02C:0x5F030] != bytes.fromhex("00 4A 10 47"):
        raise ValueError("玩家角色初始化跳板已变化")
    if struct.unpack_from("<I", rom, 0x5F030)[0] != init:
        raise ValueError("玩家角色初始化跳板目标已变化")
    if rom[(init & ~1) - 0x08000000 + 0x18:(init & ~1) - 0x08000000 + 0x1C] != bytes.fromhex("00 06 00 0E"):
        raise ValueError("图像 ID 在性别反查前的 8 位截取指令已变化")
    if struct.unpack_from("<I", rom, reverse_gender - 0x08000000 + 0x58)[0] != 0x0300500C:
        raise ValueError("性别反查中的 SaveBlock2 指针常量已变化")
    if struct.unpack_from("<I", rom, 0x15FC34)[0] != GENDER_SPECIAL + 1:
        raise ValueError("游戏脚本 special 性别读取入口已变化")


def isolated_case(rom, state, resolver, reverse_gender, variant, saved_gender):
    engine = IsolatedROM(rom, state)
    save2 = struct.unpack("<I", engine.read(0x0300500C, 4))[0]
    if not 0x02000000 <= save2 <= 0x0203FFF2:
        raise ValueError("SaveBlock2 指针越过 EWRAM")
    player_id = engine.read(AVATAR + 5, 1)[0]
    if player_id >= 16:
        raise ValueError("玩家对象 ID 超出对象事件表")
    obj = OBJECTS + player_id * OBJECT_BYTES
    if engine.read(obj + 6, 1)[0] != 0x0B:
        raise ValueError("当前样本的玩家对象不是移动类型 0x0B")
    engine.write(save2 + 8, bytes((saved_gender,)))
    engine.write(AVATAR + 7, b"\x02")
    if variant != "sample":
        engine.write(obj + 5, bytes((0 if variant == "base_male" else 7,)))
        engine.write(obj + 0x23, b"\0")
    before_gfx = engine.call(resolver, obj)
    full_gender = engine.call(reverse_gender, before_gfx)
    low_gender = engine.call(reverse_gender, before_gfx & 0xFF)
    engine.call(0x0805F02C, player_id, 2)
    cached_gender = engine.read(AVATAR + 7, 1)[0]
    after_gfx = engine.call(resolver, obj)
    if cached_gender != low_gender:
        raise ValueError("实际初始化结果与截断图像 ID 反查不符")
    engine.call(GENDER_SPECIAL)
    special_result = struct.unpack("<H", engine.read(0x020370D0, 2))[0]
    if special_result != saved_gender:
        raise ValueError("脚本 special 没有读取保存的玩家性别")
    return {
        "variant": variant,
        "saved_gender": saved_gender,
        "object_gfx_before": before_gfx,
        "gender_if_full_gfx": full_gender,
        "gender_from_low8_gfx": low_gender,
        "avatar_cache_gender_after_init": cached_gender,
        "object_gfx_after_init": after_gfx,
        "script_special_result": special_result,
    }


def flash_observation(save, state, save2):
    """Compare identity prefix and gender; no changed-value save/reload claim."""
    if len(save) < 0x20000:
        raise ValueError("Flash 文件长度不足 0x20000")
    state_block2 = state[0x21000 + save2 - 0x02000000:0x21000 + save2 - 0x02000000 + 9]
    findings = []
    for slot in range(2):
        for sector in range(14):
            base = slot * 0xE000 + sector * 0x1000
            section_id, expected, signature, _ = struct.unpack_from("<HHII", save, base + 0xFF4)
            if section_id != 0 or signature != 0x08012025:
                continue
            words = struct.unpack_from("<960I", save, base)
            total = sum(words) & 0xFFFFFFFF
            observed = ((total >> 16) + total) & 0xFFFF
            findings.append({
                "slot": slot,
                "sector": sector,
                "name_prefix_equal": save[base:base + 8] == state_block2[:8],
                "gender_equal": save[base + 8] == state_block2[8],
                "checksum_first_0xF00_equal": observed == expected,
            })
    return findings


def audit(rom, state, save=None):
    sha, (version, _, _) = release(rom)
    load_profile(rom)
    check_static(rom, version)
    init, resolver, reverse_gender = ENTRY[version]
    engine = IsolatedROM(rom, state)
    save2 = struct.unpack("<I", engine.read(0x0300500C, 4))[0]
    if not 0x02000000 <= save2 <= 0x0203FFF2:
        raise ValueError("SaveBlock2 指针越过 EWRAM")
    cases = [
        isolated_case(rom, state, resolver, reverse_gender, variant, saved)
        for variant in ("sample", "base_male", "base_female")
        for saved in (0, 1)
    ]
    trainer_records = []
    for trainer_id, expected_class in ((329, 0x51), (426, 0x59), (438, 0x5A)):
        offset = RIVAL_TRAINER_TABLE - 0x08000000 + trainer_id * 40
        raw = rom[offset:offset + 40]
        if len(raw) != 40 or raw[1] != expected_class:
            raise ValueError("劲敌训练师固定表与已核实身份不符")
        trainer_records.append({
            "trainer_id": trainer_id,
            "class": raw[1],
            "party_size": raw[32],
            "party_rom_pointer": f"{struct.unpack_from('<I', raw, 36)[0]:08X}",
        })
    return {
        "release": version,
        "rom_sha256": sha,
        "map_init_chain": ["08057106->0805EDF0", "0805EE20->0805EE3C", "0805EF60->0805F02C"],
        "player_movement_type": 11,
        "avatar_init_hook": f"{init:08X}",
        "resolved_graphics_reader": f"{resolver:08X}",
        "graphics_to_gender": f"{reverse_gender:08X}",
        "graphics_truncation_before_gender": "u16 to low u8 at init+0x18",
        "player_object_stride_bytes": OBJECT_BYTES,
        "gender_matrix": cases,
        "script_gender_special": "0806C4F0; table pointer 0815FC34; result halfword 020370D0",
        "rival_fixed_trainer_records": trainer_records,
        "flash_section0_observation": flash_observation(save, state, save2) if save is not None else None,
        "gender_edit_enabled": False,
        "scope": "The real player-object init routine was executed in isolated RAM. Full map reload, visible sprite frames, script branches choosing trainer IDs, and changed-value Flash save/reload were not executed.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--save", type=Path, help="optional matching Flash .sav for observational section 0 comparison")
    args = parser.parse_args()
    report = audit(args.rom.read_bytes(), read_state(args.state),
                   args.save.read_bytes() if args.save else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
