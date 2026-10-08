"""Audit verified Mercury ROM weekday script dispatch and object visibility.

Read-only original ROM/.ss1 inputs. Only isolated Unicorn RAM is mutated.
This audit deliberately does not clear flags in a running game or save.
"""

import argparse
import hashlib
import json
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC

from clock_data import encode_daily_event
from rom_versions import RELEASES
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU


CMD_TABLE = 0x0815F9B4
SPECIAL_TABLE = 0x0815FD60
WEEKDAY_ID = 0xDA
FLAG_SET = 0x0806E680
FLAG_CLEAR = 0x0806E6A8
FLAG_GET = 0x0806E6D0
FLAG_IDS = (0x1890, 0x1894, 0x1898, 0x18B9)
ROUTINES = {
    "1.0": {
        "entry": 0x09D64C04, "callback": 0x09D631A4,
        "callback_literal": 0x09D64C58, "clock_refresh": 0x09D56AE0,
        "weekday_branch": 0x09D6384E, "passed_entry": 0x09D64C12,
    },
    "1.2": {
        "entry": 0x09D68FD8, "callback": 0x09D67520,
        "callback_literal": 0x09D69044, "clock_refresh": 0x09D5ABD4,
        "weekday_branch": 0x09D67C20, "passed_entry": 0x09D68FE6,
    },
}
OBJECTS = (
    # flag, template, object-array, events, map-header, expected map name
    (0x1894, 0x08A193EC, 0x08A192E4, 0x08A194C0, 0x08B64AE0, "连接洞"),
    (0x1898, 0x08B14B3C, 0x08B14B0C, 0x08B14C04, 0x08BEE9CC, "擂钵山"),
    (0x18B9, 0x08A30D80, 0x08A30CF0, 0x08A31050, 0x08B607E4, "龙穴"),
)


def r16(rom, address):
    return struct.unpack_from("<H", rom, address - 0x08000000)[0]


def r32(rom, address):
    return struct.unpack_from("<I", rom, address - 0x08000000)[0]


def byte(rom, address):
    return rom[address - 0x08000000]


def literal(rom, address):
    instruction = r16(rom, address)
    if instruction & 0xF800 != 0x4800:
        raise ValueError(f"0x{address:08X} 不是 Thumb LDR 字面量")
    location = ((address + 4) & ~3) + (instruction & 0xFF) * 4
    return r32(rom, location)


def occurrences(data, needle):
    positions = []
    start = 0
    while True:
        at = data.find(needle, start)
        if at < 0:
            return positions
        positions.append(0x08000000 + at)
        start = at + 1


def script_candidates(rom, cpu):
    """Raw opcode matches are tested against the ROM's actual output pointer API."""
    direct = occurrences(rom, bytes((0x25, WEEKDAY_ID, 0)))
    output = []
    for i in range(len(rom) - 4):
        if rom[i] != 0x26 or rom[i + 3:i + 5] != bytes((WEEKDAY_ID, 0)):
            continue
        variable = struct.unpack_from("<H", rom, i + 1)[0]
        output.append({
            "address": f"0x{0x08000000 + i:08X}",
            "output_variable": f"0x{variable:04X}",
            "output_pointer": f"0x{cpu.call(0x0806E454, variable):08X}",
        })
    return {
        "special_25_raw_hits": [f"0x{x:08X}" for x in direct],
        "specialvar_26_raw_hits": output,
        "confirmed_map_script_calls": [],
        "interpretation": "Raw matches have no validated script root; both specialvar outputs resolve to NULL in actual ROM. No valid special 0xDA script consumer established.",
    }


def objects(rom, profile):
    result = []
    for flag, template, array, events, header, name in OBJECTS:
        if (r16(rom, template + 0x14) != flag
                or r32(rom, events + 4) != array
                or r32(rom, header + 4) != events):
            raise ValueError(f"对象旗标或地图指针链不符：0x{flag:04X}")
        section = byte(rom, header + 0x14)
        if profile["met_locations"].get(str(section)) != name:
            raise ValueError(f"对象所在地图名称不符：0x{flag:04X}")
        result.append({
            "flag": f"0x{flag:04X}", "template": f"0x{template:08X}",
            "script": f"0x{r32(rom, template + 0x10):08X}",
            "local_id": byte(rom, template),
            "x": struct.unpack_from("<h", rom, template - 0x08000000 + 4)[0],
            "y": struct.unpack_from("<h", rom, template - 0x08000000 + 6)[0],
            "map_section": section, "map_name": name,
        })
    return result


def make_cpu(rom, state, layout, version, current, previous, initial):
    cpu = RomCPU(rom, state, timeout_us=0)
    routine = ROUTINES[version]

    # Only the RTC cache refresh is stubbed; the callback, date comparison,
    # flag routines, and script dispatch targets remain actual ROM code.
    def keep_injected_clock(uc, address, size, data):
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    cpu.cpu.hook_add(
        UC_HOOK_CODE, keep_injected_clock,
        begin=routine["clock_refresh"], end=routine["clock_refresh"],
    )
    cpu.write(layout["clock_address"], struct.pack(
        "<H7B", current.year, 0, current.month, current.day,
        (current.weekday() + 1) % 7, 12, 0, 0,
    ))
    cpu.write(layout["daily_event"]["address"], encode_daily_event(previous))
    record = 0x03005090
    cpu.write(record, b"\0" * 40)
    cpu.write(record + 8, struct.pack("<H", 1))
    cpu.write(record + 10, struct.pack("<H", current.day))
    cpu.write(record + 12, struct.pack("<H", current.month))
    cpu.write(record + 14, struct.pack("<H", current.year))
    cpu.write(record + 16, cpu.read(layout["daily_event"]["address"], 4))
    for flag in FLAG_IDS:
        cpu.call(FLAG_SET if initial else FLAG_CLEAR, flag)
    return cpu


def audit(rom, state, profile):
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES or profile["rom_sha256"] != sha:
        raise ValueError("ROM SHA-256 与已验证配置不符")
    version = RELEASES[sha][0]
    routine = ROUTINES[version]
    layout = profile["time"]
    if (r32(rom, CMD_TABLE + 0x25 * 4) != 0x08069EFD
            or r32(rom, CMD_TABLE + 0x26 * 4) != 0x08069F3D
            or r32(rom, SPECIAL_TABLE + WEEKDAY_ID * 4)
            != layout["weekday_getter"] | 1
            or r32(rom, routine["callback_literal"]) != routine["callback"] | 1):
        raise ValueError("opcode handler、星期表槽或回调字面量不符")

    cpu = RomCPU(rom, state, timeout_us=0)
    scripts = script_candidates(rom, cpu)
    if len(scripts["special_25_raw_hits"]) != 1 or len(scripts["specialvar_26_raw_hits"]) != 2:
        raise ValueError("星期特殊函数原始候选数与已核实 ROM 不同")
    if any(int(row["output_pointer"], 16) for row in scripts["specialvar_26_raw_hits"]):
        raise ValueError("specialvar 0xDA 候选输出变量不再为无效指针")
    mapped_objects = objects(rom, profile)
    if (r32(rom, 0x08F34FCC) != 0x08F35048
            or rom[0x08F35048 - 0x08000000:0x08F3504C - 0x08000000]
            != bytes((0x2A, 0x98, 0x18, 0x02))):
        raise ValueError("0x1898 的脚本 clearflag 分支不符")
    for address, flag in ((0x08F34F2E, 0x1894), (0x08F3FBB9, 0x18B9)):
        if (byte(rom, address) != 0x29
                or r16(rom, address + 1) != flag):
            raise ValueError(f"对象脚本 setflag 0x{flag:04X} 分支不符")

    # Seven weekdays, starting with Sunday; run both initial flag polarities.
    matrix = []
    for initial in (0, 1):
        for offset in range(7):
            current = datetime(2026, 10, 4) + timedelta(days=offset)
            previous = current - timedelta(days=1)
            cpu = make_cpu(rom, state, layout, version, current, previous, initial)
            seen = []

            def mark(uc, address, size, data):
                if address == routine["weekday_branch"]:
                    seen.append(address)

            cpu.cpu.hook_add(UC_HOOK_CODE, mark)
            cpu.call(routine["callback"], 0)
            if not seen:
                raise ValueError(f"{version} {current.date()}: 未进入星期分支")
            flags_after = {f"0x{flag:04X}": cpu.call(FLAG_GET, flag)
                           for flag in FLAG_IDS}
            # V1.0 leaves the prior Sunday value unchanged and also clears it
            # on Saturday through another branch. V1.2 clears only on Sunday.
            if version == "1.0":
                expected_sunday = initial if offset == 0 else (0 if offset == 6 else 1)
            else:
                expected_sunday = 0 if offset == 0 else 1
            if flags_after["0x1898"] != expected_sunday:
                raise ValueError(f"{version} {current.date()}: 0x1898 行为改变")
            matrix.append({
                "date": current.date().isoformat(),
                "weekday_sun0": offset, "initial_flags": initial,
                "flags_after": flags_after,
                "daily_after": cpu.read(layout["daily_event"]["address"], 4).hex(),
            })

    # Prior Sunday visibility on a Friday with a future daily record: inspect
    # the real entry branch. It schedules work; it does not itself clear 0x1898.
    current = datetime(2026, 10, 9)
    previous = datetime(2026, 10, 11)
    cpu = make_cpu(rom, state, layout, version, current, previous, 0)
    passed = []

    def branch(uc, address, size, data):
        if address == routine["passed_entry"]:
            passed.append(address)

    cpu.cpu.hook_add(UC_HOOK_CODE, branch)
    cpu.call(routine["entry"])
    expected_pass = version == "1.2"
    if bool(passed) != expected_pass or cpu.call(FLAG_GET, 0x1898) != 0:
        raise ValueError("未来日期入口分支或入口的直接旗标作用改变")

    return {
        "rom_version": version, "rom_sha256": sha,
        "handlers": {
            "special_cmd_25": "0x08069EFC",
            "specialvar_cmd_26": "0x08069F3C",
            "special_table_slot": "0x081600C8",
            "weekday_getter": f"0x{layout['weekday_getter']:08X}",
            "daily_entry": f"0x{routine['entry']:08X}",
            "registered_callback": f"0x{routine['callback']:08X}",
            "weekday_flag_branch": f"0x{routine['weekday_branch']:08X}",
        },
        "special_da_candidates": scripts,
        "map_objects": mapped_objects,
        "additional_script_clear_1898": {
            "script": "0x08F35048", "pointer_from": "0x08F34FCC",
            "bytes": "2a981802", "meaning": "clearflag 0x1898; end",
        },
        "object_script_setflags": [
            {"flag": "0x1894", "opcode": "setflag", "address": "0x08F34F2E"},
            {"flag": "0x18B9", "opcode": "setflag", "address": "0x08F3FBB9"},
        ],
        "matrix": matrix,
        "future_record_friday": {
            "current": current.date().isoformat(),
            "record": previous.date().isoformat(),
            "passed_entry_date_gate": bool(passed),
            "flag_1898_after_entry": 0,
        },
        "scope": "Exact ROM code in isolated CPU with only RTC cache refresh stubbed. Map script execution, capture/reward consumption, live transitions and save/reload unverified; no original file changed.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    state = read_state(args.state)
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    result = audit(rom, state, profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['rom_version']}: weekday script audit passed ({len(result['matrix'])} callback cases)")


if __name__ == "__main__":
    main()
