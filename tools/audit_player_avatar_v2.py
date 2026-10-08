"""Bounded, offline avatar audit for the two exact supported Mercury ROMs.

The supplied ROM and mGBA state are only read. All perturbations happen inside
RomCPU's private Unicorn memory; no running emulator or save is contacted.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)

from rom_versions import load_profile, release
from tools.read_state import read_state

IDENTITY = {
    "1.0": (0x09D0D301, 0x09D0D321, 0x09D0D20C, 0x09D0D24C),
    "1.2": (0x09D0F785, 0x09D0F7A5, 0x09D0F690, 0x09D0F6D0),
}
VARIABLES = (0x501F, 0x5020, 0x5021, 0x5022, 0x503D, 0x5023, 0x5025)
AVATAR = 0x02037078
GFX_ENTRY = 0x0805C808
GFX_EXPLICIT = 0x0805C7C8
SENTINEL = 0x1234


class IsolatedROM:
    def __init__(self, rom, state):
        if len(state) != 0x61000:
            raise ValueError("即时存档解码长度错误")
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for address, size, data in (
            (0x08000000, len(rom), rom),
            (0x02000000, 0x40000, state[0x21000:]),
            (0x03000000, 0x8000, state[0x19000:0x21000]),
        ):
            self.cpu.mem_map(address, size)
            self.cpu.mem_write(address, data)

    def call(self, address, *args):
        for reg, arg in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            self.cpu.reg_write(reg, arg)
        self.cpu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        self.cpu.reg_write(UC_ARM_REG_LR, 0x02001101)
        self.cpu.emu_start(address | 1, 0x02001100, count=1000000)
        if self.cpu.reg_read(UC_ARM_REG_PC) != 0x02001100:
            raise ValueError(f"ROM 子程序未在上限内返回：{address:08X}")
        return self.cpu.reg_read(UC_ARM_REG_R0)

    def read(self, address, size):
        return bytes(self.cpu.mem_read(address, size))

    def write(self, address, data):
        self.cpu.mem_write(address, data)


def thumb_bl_callers(rom, target):
    """Return exact Thumb BL sites; an indirect caller will not appear here."""
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


def graphics(engine, explicit=False):
    routine = GFX_EXPLICIT if explicit else GFX_ENTRY
    return [
        [engine.call(routine, state, gender) if explicit else engine.call(routine, state)
         for state in range(8)]
        for gender in (0, 1)
    ]


def audit(rom, state):
    sha, (version, _, _) = release(rom)
    load_profile(rom)  # Also checks the checked-in profile's complete identity.
    explicit_hook, cached_hook, override_entry, vars_offset = IDENTITY[version]
    if struct.unpack_from("<I", rom, 0x5C7E4)[0] != explicit_hook:
        raise ValueError("显式性别图像入口跳转目标与已审计 ROM 不符")
    if struct.unpack_from("<I", rom, 0x5C80C)[0] != cached_hook:
        raise ValueError("缓存性别图像入口跳转目标与已审计 ROM 不符")
    if struct.unpack_from("<7I", rom, vars_offset - 0x08000000) != VARIABLES:
        raise ValueError("外观覆盖变量表与已审计 ROM 不符")
    if struct.unpack_from("<I", rom, 0x5C96C)[0] != AVATAR:
        raise ValueError("角色缓存地址与已审计 ROM 不符")
    if rom[0x5CAE4:0x5CAE8] != bytes.fromhex("41 46 C1 71"):
        raise ValueError("角色初始化候选例程的缓存性别写入指令已变化")

    engine = IsolatedROM(rom, state)
    save2 = struct.unpack("<I", engine.read(0x0300500C, 4))[0]
    if not 0x02000000 <= save2 <= 0x0203FFF2:
        raise ValueError("SaveBlock2 指针越过 EWRAM")
    saved = engine.read(save2 + 8, 1)
    cached = engine.read(AVATAR + 7, 1)
    if saved[0] not in (0, 1) or cached[0] not in (0, 1):
        raise ValueError("样本性别值不在已审计的双值范围")

    pointers = [engine.call(0x0806E454, var) for var in VARIABLES]
    if len(set(pointers)) != len(VARIABLES) or any(
        not 0x02000000 <= pointer <= 0x0203FFFE for pointer in pointers
    ):
        raise ValueError("覆盖变量没有映射到独立 EWRAM 半字")
    originals = [engine.read(pointer, 2) for pointer in pointers]

    # Each row uses an isolated change and restores it before the next row.
    try:
        original = graphics(engine, explicit=True)
        saved_only = []
        for gender in (0, 1):
            engine.write(save2 + 8, bytes((gender,)))
            saved_only.append([engine.call(GFX_ENTRY, s) for s in range(8)])
        engine.write(save2 + 8, saved)
        cache_only = []
        for gender in (0, 1):
            engine.write(AVATAR + 7, bytes((gender,)))
            cache_only.append([engine.call(GFX_ENTRY, s) for s in range(8)])
        engine.write(AVATAR + 7, cached)

        variable_effects = {}
        for var, pointer, old in zip(VARIABLES, pointers, originals):
            engine.write(pointer, struct.pack("<H", SENTINEL))
            changed = graphics(engine, explicit=True)
            engine.write(pointer, old)
            variable_effects[f"{var:04X}"] = [
                state for state in range(8)
                if any(changed[g][state] != original[g][state] for g in (0, 1))
            ]

        for pointer in pointers:
            engine.write(pointer, b"\0\0")
        no_overrides = graphics(engine, explicit=True)
    finally:
        engine.write(save2 + 8, saved)
        engine.write(AVATAR + 7, cached)
        for pointer, old in zip(pointers, originals):
            engine.write(pointer, old)

    if saved_only[0] != saved_only[1]:
        raise ValueError("仅更改持久性别竟改变了缓存图像入口")
    if no_overrides[0][:5] == no_overrides[1][:5]:
        raise ValueError("无覆盖时基础性别图像没有分离")
    if version == "1.2" and original[0][:5] != original[1][:5]:
        raise ValueError("V1.2 样本前五状态的覆盖行为与预期不符")

    callers = thumb_bl_callers(rom, GFX_ENTRY)
    if not callers:
        raise ValueError("ROM 中未找到实际图像入口的直接调用")
    override_callers = thumb_bl_callers(rom, override_entry)
    if (explicit_hook & ~1) + 6 not in override_callers:
        raise ValueError("显式图像跳转目标没有调用预期覆盖例程")
    return {
        "release": version,
        "rom_sha256": sha,
        "hook_targets": [f"{explicit_hook:08X}", f"{cached_hook:08X}"],
        "override_entry": f"{override_entry:08X}",
        "override_entry_bl_callers": [f"{caller:08X}" for caller in override_callers],
        "override_variable_table": f"{vars_offset:08X}",
        "override_active_in_sample": {
            f"{var:04X}": struct.unpack("<H", old)[0] != 0
            for var, old in zip(VARIABLES, originals)
        },
        "variable_effect_states": variable_effects,
        "explicit_gender_graphics": original,
        "zeroed_override_graphics": no_overrides,
        "save_only_graphics": saved_only,
        "cache_only_graphics": cache_only,
        "graphics_reader_bl_callers": [f"{caller:08X}" for caller in callers],
        "explicit_gender_bl_callers": [
            f"{caller:08X}" for caller in thumb_bl_callers(rom, GFX_EXPLICIT)
        ],
        "cache_seed_candidate": "0805CAE4: mov r1,r8; 0805CAE6: strb r1,[r0,#7]",
        "cache_seed_candidate_direct_bl_callers": [
            f"{caller:08X}" for caller in thumb_bl_callers(rom, 0x0805CA3C)
        ],
        "gender_edit_enabled": False,
        "scope": "Offline isolated ROM calls and direct BL scan only; sprite rebuild, map transitions, story/rival team, and save/reload not established.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="exact V1.0 or V1.2 GBA ROM")
    parser.add_argument("state", type=Path, help="mGBA state from the same release")
    args = parser.parse_args()
    print(json.dumps(audit(args.rom.read_bytes(), read_state(args.state)),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
