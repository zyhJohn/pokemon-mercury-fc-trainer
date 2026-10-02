"""Read-only local samples and isolated actual-ROM calendar/playtime checks.

Only the hardware RTC serial read is stubbed; injected BCD samples then pass
through the actual ROM conversion and weekday accessor. No live RAM is written.
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

from clock_data import (
    decode_game_clock,
    encode_daily_event,
    encode_footer,
    local_epoch,
    read_save,
)
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state, timeout_us=0)
    layout = profile["time"]
    memory = StateMemory(state, rom)
    snapshot = Trainer(memory, profile, "backups").snapshot_time()

    # The ROM routine refreshes sRtc by reading serial GPIO. Stub that hardware
    # read and keep the explicit BCD sample, not the calendar conversion code.
    def skip_hardware(cpu, address, size, user_data):
        cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    engine.cpu.hook_add(UC_HOOK_CODE, skip_hardware, begin=0x09D569D4, end=0x09D569D4)
    clock_cases = 0
    monday = datetime(2026, 9, 28)
    dates = [monday + timedelta(days=n) for n in range(7)] + [
        datetime(2000, 1, 1),
        datetime(2024, 2, 29, 23, 59, 59),
        datetime(2024, 3, 1),
        datetime(2026, 12, 31, 23, 59, 59),
        datetime(2027, 1, 1),
        datetime(2099, 12, 31, 23, 59, 59),
    ]
    for value in dates:
        for invert in (0, 1):
            engine.write(
                layout["rtc_cache_address"],
                encode_footer(value, local_epoch(value))[:8],
            )
            engine.write(layout["rtc_error_address"], b"\0\0")
            engine.write(layout["invert_ampm_address"], bytes([invert]))
            engine.write(layout["clock_address"] + 9, b"\0")
            engine.call(layout["clock_update"])
            expected = value.replace(hour=(value.hour + 12) % 24) if invert else value
            actual, mismatch = decode_game_clock(
                engine.read(layout["clock_address"], 9)
            )
            if actual != expected or mismatch:
                raise ValueError("实际 ROM 日历转换或 AM/PM 对调不同")
            if engine.call(layout["weekday_getter"]) != (value.weekday() + 1) % 7:
                raise ValueError("实际 ROM 星期读取不同")
            clock_cases += 1
    play_address = snapshot["address"] + 14
    play_cases = [
        ((0, 0, 0, 255), (0, 0, 1, 0)),
        ((26, 4, 59, 255), (26, 5, 0, 0)),
        ((26, 59, 59, 255), (27, 0, 0, 0)),
        ((999, 59, 59, 255), (999, 59, 59, 59)),
    ]
    for before, expected in play_cases:
        engine.write(play_address, struct.pack("<HBBB", *before))
        engine.write(0x03000E7C, b"\1")  # ROM's sPlayTimeCounterState
        engine.write(0x0300539C, struct.pack("<I", 58))  # previous RTC second
        engine.write(layout["clock_address"] + 8, b"\x3b")
        engine.write(layout["rtc_error_address"], b"\0\0")
        neighbours = engine.read(play_address - 1, 1) + engine.read(play_address + 5, 1)
        engine.call(layout["playtime_update"])
        if engine.read(play_address, 5) != struct.pack("<HBBB", *expected):
            raise ValueError(
                f"实际 ROM 时长进位或上限不同：{before} -> {engine.read(play_address, 5).hex()}，预期 {expected}"
            )
        if neighbours != engine.read(play_address - 1, 1) + engine.read(
            play_address + 5, 1
        ):
            raise ValueError("实际 ROM 时长更新改变邻近字段")
    daily = layout["daily_event"]
    if engine.call(0x0806E454, daily["variable"]) != daily["address"]:
        raise ValueError("实际 ROM 每日变量地址不同")
    value = datetime(2026, 10, 3, 12, 34)
    engine.write(
        layout["clock_address"],
        struct.pack(
            "<H7B",
            value.year,
            0,
            value.month,
            value.day,
            (value.weekday() + 1) % 7,
            value.hour,
            value.minute,
            0,
        ),
    )
    daily_cases = 0
    for day_offset, allowed in [(1, False), (0, False), (-1, True)]:
        before = encode_daily_event(value + timedelta(days=day_offset))
        engine.write(daily["address"], before)
        if bool(engine.call(daily["future_checker"], daily["variable"])) != (
            day_offset > 0
        ):
            raise ValueError("实际 ROM 未来日期判断不同")
        if bool(engine.call(daily["checker"], daily["variable"], 1)) != allowed:
            raise ValueError("实际 ROM 每日事件允许状态不同")
        expected = encode_daily_event(value) if allowed else before
        if engine.read(daily["address"], 4) != expected:
            raise ValueError("实际 ROM 每日事件记录更新不同")
        daily_cases += 1
    # Prove the four bytes' persistent mapping through the actual serializer.
    original = memory.read(daily["address"], 4)
    engine.write(daily["address"], original)
    expanded = profile["economy"]["expanded_save"]
    scratch = 0x02000000
    engine.write(scratch, b"\0" * 0x1000)
    engine.write(scratch + 0xFF4, struct.pack("<H", daily["section_id"]))
    engine.write(expanded["buffer_pointer"], struct.pack("<I", scratch))
    engine.call(expanded["serializer"])
    if engine.read(scratch + daily["section_offset"] - 16, 36) != memory.read(
        daily["address"] - 16, 36
    ):
        raise ValueError("实际 ROM 每日日期存档序列化不同")
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "clock_conversion_and_weekday_cases": clock_cases,
        "playtime_rollover_cases": len(play_cases),
        "daily_event_cases": daily_cases,
        "daily_event_serialization": True,
        "state_calendar": snapshot["clock"].isoformat(),
        "state_playtime": snapshot["playtime"],
        "scope": "Actual ROM routines with RTC hardware read stubbed; no live emulator write or gameplay reload acceptance.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--profile", type=Path, default=Path("rom_profile.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    result = verify(args.rom.read_bytes(), read_state(args.state), profile)
    if args.save:
        info = read_save(args.save, args.rom, profile)
        result["persistent_sample"] = {
            "calendar": info["saved"].isoformat(),
            "offset": info["offset"],
            "playtime": info["playtime"],
            "counter": info["counter"],
            "weekday_mismatch": info["weekday_mismatch"],
        }
        daily = profile["time"]["daily_event"]
        match = False
        for base in (0, 0xE000):
            for n in range(14):
                offset = base + n * 0x1000
                ident, _, _, counter = struct.unpack_from(
                    "<HHII", info["data"], offset + 0xFF4
                )
                if ident == daily["section_id"] and counter == info["counter"]:
                    payload = offset + daily["section_offset"]
                    match = info["data"][payload - 16 : payload + 20] == StateMemory(
                        read_state(args.state), args.rom.read_bytes()
                    ).read(daily["address"] - 16, 36)
        result["daily_persistent_neighbourhood_match"] = match
        if not match:
            raise ValueError("最新 .sav 每日日期与即时存档邻近字节不同；样本可能不配对")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Time ROM checks passed; private report:", args.output)


if __name__ == "__main__":
    main()
