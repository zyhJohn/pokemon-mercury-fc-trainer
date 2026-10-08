"""Bounded, read-only audit of the two verified ROMs' daily/weekday paths.

Runs actual ROM routines in isolated Unicorn memory. The input ROM, .sav and
.ss1 are only read; the generated report belongs in an ignored private folder.
This does not execute map scripts or establish that a particular NPC reward is
available after a save/reload.
"""

import argparse
import hashlib
import json
import struct
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clock_data import decode_daily_event, encode_daily_event, read_save
from rom_versions import RELEASES
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU


SPECIAL_TABLE = 0x0815FD60
WEEKDAY_SPECIAL = 0xDA
DAILY_ENTRY = {"1.0": 0x09D64C04, "1.2": 0x09D68FD8}
ENTRY_CALLER = {"1.0": 0x09D4C3CE, "1.2": 0x09D4F64E}
SECOND_CHECK_CALL = {"1.0": 0x09D63208, "1.2": 0x09D675AC}


def u16(rom, address):
    return struct.unpack_from("<H", rom, address - 0x08000000)[0]


def u32(rom, address):
    return struct.unpack_from("<I", rom, address - 0x08000000)[0]


def literal(rom, address):
    instruction = u16(rom, address)
    if instruction & 0xF800 != 0x4800:
        raise ValueError(f"预期 LDR 字面量：0x{address:08X}")
    location = ((address + 4) & ~3) + (instruction & 0xFF) * 4
    return u32(rom, location)


def bl_dest(rom, address):
    first, second = u16(rom, address), u16(rom, address + 2)
    if first & 0xF800 != 0xF000 or second & 0xF800 != 0xF800:
        raise ValueError(f"预期 Thumb BL：0x{address:08X}")
    displacement = ((first & 0x7FF) << 12) | ((second & 0x7FF) << 1)
    if displacement & 0x400000:
        displacement -= 0x800000
    return address + 4 + displacement


def scan_bl(rom, targets):
    """Find exact Thumb-1 BL encodings; hits still require code-context review."""
    result = {address: [] for address in targets}
    for offset in range(0, len(rom) - 3, 2):
        first, second = struct.unpack_from("<HH", rom, offset)
        if first & 0xF800 != 0xF000 or second & 0xF800 != 0xF800:
            continue
        displacement = ((first & 0x7FF) << 12) | ((second & 0x7FF) << 1)
        if displacement & 0x400000:
            displacement -= 0x800000
        target = 0x08000000 + offset + 4 + displacement
        if target in result:
            result[target].append(0x08000000 + offset)
    return {f"0x{key:08X}": [f"0x{a:08X}" for a in values]
            for key, values in result.items()}


def audit(rom, state, state_file_sha256, profile, save_path, rom_path):
    sha = hashlib.sha256(rom).hexdigest()
    if sha != profile["rom_sha256"] or sha not in RELEASES:
        raise ValueError("ROM SHA-256 与已验证配置不匹配")
    version = RELEASES[sha][0]
    layout = profile["time"]
    daily = layout["daily_event"]
    cpu = RomCPU(rom, state, timeout_us=0)
    original = StateMemory(state, rom)
    if cpu.call(0x0806E454, daily["variable"]) != daily["address"]:
        raise ValueError("Var5009 地址与实际 ROM 不一致")

    weekday_slot = SPECIAL_TABLE + 4 * WEEKDAY_SPECIAL
    if u32(rom, weekday_slot) != layout["weekday_getter"] | 1:
        raise ValueError("星期特殊函数表项与实际 ROM 不一致")
    entry = DAILY_ENTRY[version]
    caller = ENTRY_CALLER[version]
    second = SECOND_CHECK_CALL[version]
    if (bl_dest(rom, entry + (6 if version == "1.0" else 6)) != daily["checker"]
            or literal(rom, entry + 4) != daily["variable"]
            or bl_dest(rom, caller) != entry
            or bl_dest(rom, second) != daily["checker"]
            or literal(rom, second - 2) != daily["variable"]):
        raise ValueError("每日事件入口或第二调用点与 ROM 指令不一致")

    # A Sunday boundary, a weekly boundary, a future-date rollback and a leap
    # boundary. Mode 0 is a query; mode 1 is the ROM's conditional update.
    transitions = [
        ("Saturday_to_Sunday", datetime(2026, 10, 3), datetime(2026, 10, 4)),
        ("Sunday_to_Monday", datetime(2026, 10, 4), datetime(2026, 10, 5)),
        ("same_Sunday", datetime(2026, 10, 4), datetime(2026, 10, 4)),
        ("future_to_Sunday", datetime(2026, 10, 5), datetime(2026, 10, 4)),
        ("leap_day_to_March", datetime(2024, 2, 29), datetime(2024, 3, 1)),
        ("year_boundary", datetime(2026, 12, 31), datetime(2027, 1, 1)),
    ]
    rows = []
    for name, previous, current in transitions:
        cpu.write(layout["clock_address"], struct.pack(
            "<H7B", current.year, 0, current.month, current.day,
            (current.weekday() + 1) % 7, current.hour, current.minute,
            current.second,
        ))
        weekday = cpu.call(layout["weekday_getter"])
        if weekday != (current.weekday() + 1) % 7:
            raise ValueError(f"{name}: 星期读取不一致")
        for update in (0, 1):
            cpu.write(daily["address"], encode_daily_event(previous))
            neighborhood = cpu.read(daily["address"] - 8, 20)
            future = bool(cpu.call(daily["future_checker"], daily["variable"]))
            if future != (previous > current):
                raise ValueError(f"{name}: 未来日期检查器异常")
            allowed = bool(cpu.call(daily["checker"], daily["variable"], update))
            after = cpu.read(daily["address"] - 8, 20)
            expected_allowed = previous != current and (
                version == "1.2" or previous < current)
            expected_record = (current if update and expected_allowed else previous)
            if (allowed != expected_allowed
                    or decode_daily_event(after[8:12]) != expected_record
                    or after[:8] != neighborhood[:8]
                    or after[12:] != neighborhood[12:]):
                raise ValueError(f"{name} mode={update}: 每日检查器或邻字节异常")
            rows.append({
                "transition": name, "previous": previous.date().isoformat(),
                "current": current.date().isoformat(), "weekday": weekday,
                "update_mode": update, "future": future, "allowed": allowed,
                "record_after": expected_record.date().isoformat(),
                "neighbor_bytes_preserved": True,
            })

    save = read_save(save_path, rom_path, profile)
    sample_raw = original.read(daily["address"], 4)
    section_matches = []
    for base in (0, 0xE000):
        for number in range(14):
            offset = base + number * 0x1000
            section_id, _, _, counter = struct.unpack_from("<HHII", save["data"], offset + 0xFF4)
            if section_id == daily["section_id"] and counter == save["counter"]:
                flash_at = offset + daily["section_offset"]
                section_matches.append({
                    "flash_offset": f"0x{flash_at:X}",
                    "daily_raw": save["data"][flash_at:flash_at + 4].hex(),
                    "daily_decoded": (decode_daily_event(save["data"][flash_at:flash_at + 4]).isoformat()
                                      if decode_daily_event(save["data"][flash_at:flash_at + 4]) else None),
                    "daily_equal_to_state": save["data"][flash_at:flash_at + 4] == sample_raw,
                    "neighbors_equal_to_state": save["data"][flash_at - 16:flash_at + 20]
                    == original.read(daily["address"] - 16, 36),
                })

    targets = [daily["checker"], daily["updater"], daily["future_checker"],
               layout["weekday_getter"], entry]
    return {
        "rom_version": version, "rom_sha256": sha,
        "input_sha256": {
            "sav": save["sha256"],
            "ss1": state_file_sha256,
        },
        "addresses": {
            "daily_variable": "0x5009/0x500A",
            "daily_ram": f"0x{daily['address']:08X}",
            "daily_checker": f"0x{daily['checker']:08X}",
            "daily_updater": f"0x{daily['updater']:08X}",
            "future_checker": f"0x{daily['future_checker']:08X}",
            "weekday_getter": f"0x{layout['weekday_getter']:08X}",
            "weekday_special_table_slot": f"0x{weekday_slot:08X}",
            "weekday_special_id": f"0x{WEEKDAY_SPECIAL:X}",
            "daily_entry": f"0x{entry:08X}",
            "daily_entry_caller_bl": f"0x{caller:08X}",
            "second_checker_bl": f"0x{second:08X}",
        },
        "static_bl_hits_unreviewed_except_named_call_sites": scan_bl(rom, targets),
        "matrix": rows,
        "sample": {
            "state_daily_raw": sample_raw.hex(),
            "state_daily_decoded": (decode_daily_event(sample_raw).isoformat()
                                    if decode_daily_event(sample_raw) else None),
            "save_section4": section_matches,
            "rtc_saved_calendar": save["saved"].isoformat(),
        },
        "scope": "Actual ROM routines in isolated CPU; script/NPC rewards, map transitions, live save/reload and original files are not changed or accepted.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--save", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    rom = args.rom.read_bytes()
    state_file = args.state.read_bytes()
    state = read_state(args.state)
    result = audit(rom, state, hashlib.sha256(state_file).hexdigest(),
                   profile, args.save, args.rom)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['rom_version']} daily audit passed: {len(result['matrix'])} ROM cases; private report: {args.output}")


if __name__ == "__main__":
    main()
