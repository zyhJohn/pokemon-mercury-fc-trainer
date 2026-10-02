"""Isolated ROM currency routines and actual expanded-save serialization.

Optional --save compares a user's paired persistent save read-only. No files or
live emulator RAM are modified. Private values belong only in the report.
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.read_state import read_state, StateMemory
from tools.verify_rom_routines import RomCPU
from trainer_core import Trainer


def verify(rom, state, profile, save=None):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state, timeout_us=0)
    trainer = Trainer(StateMemory(state, rom), profile, "backups")
    snapshot = trainer.snapshot()
    layout = profile["economy"]
    values = {key: snapshot[key] for key in ("coins", "beauty_points", "bracer_points")}
    checks = 0
    coins = layout["coins"]
    if engine.call(coins["getter"]) != snapshot["coins"]:
        raise ValueError("扩展代币读取不同")
    for value in [0, 1, 735, 65535, 65536, coins["maximum"]]:
        old = engine.read(coins["address"] - 4, 12)
        engine.call(coins["setter"], value)
        actual = engine.read(coins["address"] - 4, 12)
        if (
            actual != old[:4] + struct.pack("<I", value) + old[8:]
            or engine.call(coins["getter"]) != value
        ):
            raise ValueError("扩展代币写入或邻近字节不一致")
        engine.write(coins["address"] - 4, old)
        checks += 1
    engine.call(coins["setter"], coins["maximum"] - 1)
    engine.call(0x9D59F3C, 10)
    if engine.call(coins["getter"]) != coins["maximum"]:
        raise ValueError("代币上限与实际增加函数不同")
    engine.write(coins["address"], snapshot["values_raw"]["coins"])
    checks += 1
    for key in ["beauty_points", "bracer_points"]:
        field = layout[key]
        if (
            engine.call(0x806E454, field["variable"]) != field["address"]
            or engine.call(0x806E568, field["variable"]) != snapshot[key]
        ):
            raise ValueError("点数变量定位或读取不同")
        for value in [0, 1, snapshot[key], 65535]:
            old = engine.read(field["address"] - 2, 6)
            engine.call(0x806E584, field["variable"], value)
            if (
                engine.read(field["address"] - 2, 6)
                != old[:2] + struct.pack("<H", value) + old[4:]
                or engine.call(0x806E568, field["variable"]) != value
            ):
                raise ValueError("点数变量写入或邻近字节不一致")
            engine.write(field["address"] - 2, old)
            checks += 1
    save2 = snapshot["money_save2"]
    key_address = save2 + layout["security_key_offset"]
    money_address = snapshot["saveblock"] + layout["money_offset"]
    old_key, old_money = engine.read(key_address, 4), engine.read(money_address, 4)
    for key in [0, 0xAABBCCDD]:
        engine.write(key_address, struct.pack("<I", key))
        for value in [0, 1, layout["money_maximum"]]:
            engine.call(0x809FD70, money_address, value)
            if (
                engine.read(money_address, 4) != struct.pack("<I", value ^ key)
                or engine.call(0x809FD58, money_address) != value
            ):
                raise ValueError("金钱编码与实际ROM不同")
            checks += 1
    engine.write(key_address, old_key)
    engine.write(money_address, old_money)
    expanded = layout["expanded_save"]
    scratch = 0x2000000
    original = engine.read(expanded["ram_address"], expanded["size"])
    engine.write(scratch, b"\0" * 0x1000)
    engine.write(scratch + 0xFF4, struct.pack("<H", expanded["section_id"]))
    engine.write(expanded["buffer_pointer"], struct.pack("<I", scratch))
    engine.call(expanded["serializer"])
    actual = engine.read(scratch + expanded["section_offset"], expanded["size"])
    if actual != original:
        raise ValueError("实际扩展存档序列化不同")
    save_match = None
    if save is not None:
        if len(save) < 0x1C000:
            raise ValueError("持久存档长度不足")
        candidates = []
        for base in [0, 0xE000]:
            sectors = [
                save[base + n * 0x1000 : base + (n + 1) * 0x1000] for n in range(14)
            ]
            headers = [struct.unpack_from("<HHII", sector, 0xFF4) for sector in sectors]
            if (
                {h[0] for h in headers} != set(range(14))
                or any(h[2] != 0x08012025 for h in headers)
                or len({h[3] for h in headers}) != 1
            ):
                continue
            index = next(
                i for i, h in enumerate(headers) if h[0] == expanded["section_id"]
            )
            candidates.append((headers[index][3], sectors[index]))
        if not candidates:
            raise ValueError("没有完整且编号一致的存档槽")
        counter, sector = max(candidates, key=lambda pair: pair[0])
        save_match = {}
        for key in values:
            field = layout[key]
            offset = (
                expanded["section_offset"] + field["address"] - expanded["ram_address"]
            )
            # Match neighbours as well as the reported small values.
            expected = StateMemory(state, rom).read(
                field["address"] - 16, 32 + field["size"]
            )
            if sector[offset - 16 : offset + field["size"] + 16] != expected:
                raise ValueError(f"{key}：最新存档与即时存档不配对")
            save_match[key] = {
                "section_offset": offset,
                "value": int.from_bytes(
                    sector[offset : offset + field["size"]], "little"
                ),
            }
        save_match["save_counter"] = counter
    sort_checks = []
    for pocket in profile["pockets"]:
        snap = trainer.snapshot(pocket["id"])
        a, before, after = trainer.sort_bag(snap)[0]
        before_records = list(struct.iter_unpack("<HH", before))
        after_records = list(struct.iter_unpack("<HH", after))
        if Counter(before_records) != Counter(after_records) or after_records != sorted(
            before_records, key=lambda record: (record[0] == 0, record[0])
        ):
            raise ValueError("背包排序丢失或改变记录")
        sort_checks.append(
            {
                "pocket": pocket["id"],
                "slots": pocket["capacity"],
                "changed": before != after,
            }
        )
    return {
        "passed": True,
        "rom_sha256": profile["rom_sha256"],
        "values": values,
        "actual_routine_checks": checks,
        "actual_save_serialization": True,
        "persistent_save_match": save_match,
        "bag_sort_checks": sort_checks,
        "scope": "Isolated actual ROM read/write and serialization plus read-only paired save evidence; not live save/reload or game menu validation.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("rom_profile.json")
        .read_text(encoding="utf-8")
    )
    result = verify(
        args.rom.read_bytes(),
        read_state(args.state),
        profile,
        args.save.read_bytes() if args.save else None,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
