"""Offline ROM/state/save evidence for box names and repel; never edits user files."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from game_fields import (
    box_name_address,
    decode_box_name,
    encode_box_name,
    prepare_box_name_patch,
    prepare_repel_steps_patch,
    read_box_name,
    read_repel_steps,
)
from rom_versions import load_profile
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU


LAYOUT = json.loads(
    Path(__file__).resolve().parents[1].joinpath("game_fields_layout.json").read_text(
        encoding="utf-8"
    )
)
BOX_LAYOUT = LAYOUT["box_names"]
REPEL_LAYOUT = LAYOUT["repel_steps"]


def verify_box_names(rom: bytes, state: bytes, save: bytes) -> dict:
    profile = load_profile(rom)
    sha = profile["rom_sha256"]
    release = BOX_LAYOUT["verified_releases"][sha]
    memory = StateMemory(state, rom)
    engine = RomCPU(rom, state)
    table = release["pointer_table"]
    getter = release["getter"]
    addresses = []
    original = []
    for index in range(25):
        address = box_name_address(sha, index)
        actual_table = struct.unpack_from("<I", rom, table - 0x08000000 + 4 * index)[0]
        actual_getter = engine.call(getter, index)
        if address != actual_table or address != actual_getter:
            raise ValueError(f"盒{index + 1}地址与实际ROM表/函数不一致")
        name, raw = read_box_name(memory, sha, index)
        if not name or raw != engine.read(address, 9):
            raise ValueError(f"盒{index + 1}即时存档解码不一致")
        addresses.append(address)
        original.append(raw)
    if engine.call(getter, 25) != 0:
        raise ValueError("盒名getter未拒绝索引25")
    if sorted(addresses) != list(range(min(addresses), max(addresses) + 1, 9)):
        raise ValueError("25个名称地址未构成9字节记录")
    if min(addresses) != 0x020315F5:
        raise ValueError("名称基址不一致")

    section_ptr = memory.r32(BOX_LAYOUT["save_section_source_pointer"])
    if section_ptr + BOX_LAYOUT["save_section_offset"] != min(addresses):
        raise ValueError("section13源地址与盒名不一致")
    source_names = memory.read(min(addresses), 25 * 9)
    sectors = []
    for sector in range(min(32, len(save) // 0x1000)):
        base = sector * 0x1000
        section_id = struct.unpack_from("<H", save, base + 0xFF4)[0]
        if section_id != BOX_LAYOUT["save_section"]:
            continue
        data = save[base + BOX_LAYOUT["save_section_offset"] : base + BOX_LAYOUT["save_section_offset"] + 225]
        if data == source_names:
            sectors.append(base)
    if not sectors:
        raise ValueError("最新.sav未找到与即时存档一致的section13盒名")

    cases = ["A", "ABCDEFGH", "大力鳄", "大力鳄A1", "小智Ab12"]
    for index in (0, 12, 13, 14, 24):
        address = addresses[index]
        before = engine.read(address, 9)
        for name in cases:
            patch = prepare_box_name_patch(memory, sha, index, name)
            if patch.address != address or patch.before != before:
                raise ValueError("盒名补丁前值或地址错误")
            engine.write(address, patch.after)
            result_ptr = engine.call(getter, index)
            if result_ptr != address or decode_box_name(engine.read(result_ptr, 9)) != name:
                raise ValueError("修改后的盒名未由实际ROM getter读取")
            engine.write(address, before)
    for invalid in ("ABCDEFGHI", "大力鳄A12", "😀", ""):
        try:
            encode_box_name(invalid)
        except ValueError:
            continue
        raise ValueError(f"无效盒名未拒绝: {invalid!r}")

    return {
        "passed": True,
        "rom_sha256": sha,
        "box_name_getter": hex(getter),
        "box_name_pointer_table": hex(table),
        "box_count": len(addresses),
        "record_bytes": 9,
        "max_payload_bytes": 8,
        "terminator": "ff",
        "first_index_address": hex(addresses[0]),
        "last_index_address": hex(addresses[-1]),
        "save_section_source": hex(section_ptr),
        "save_section": 13,
        "save_section_offset": hex(BOX_LAYOUT["save_section_offset"]),
        "matching_save_sectors": [hex(offset) for offset in sectors],
        "isolated_getter_cases": 25,
        "scope": "Actual ROM getter and pointer table, current state, and paired save bytes; no live menu or save/reload after editing.",
    }


def verify_repel(rom: bytes, state: bytes, save: bytes) -> dict:
    profile = load_profile(rom)
    sha = profile["rom_sha256"]
    layout = REPEL_LAYOUT
    release = layout["verified_releases"][sha]
    memory = StateMemory(state, rom)
    value, raw = read_repel_steps(memory, sha)
    engine = RomCPU(rom, state)
    actual_pointer = engine.call(0x0806E454, layout["variable_id"])
    if actual_pointer != layout["address"]:
        raise ValueError("喷雾变量ROM解析地址不一致")
    if engine.call(0x0806E568, layout["variable_id"]) != value:
        raise ValueError("喷雾变量ROM读取值不一致")
    hook = struct.unpack_from("<I", rom, layout["decrement_entry"] - 0x08000000 + 4)[0]
    if hook != release["decrement_hook"]:
        raise ValueError("喷雾递减入口跳转目标与已核验ROM不一致")
    for item, expected in layout["verified_items"].items():
        if engine.call(layout["item_step_getter"], int(item)) != expected:
            raise ValueError(f"喷雾道具{item}步数与ROM不同")
    for item in (1, 82, 85):
        if engine.call(layout["item_step_getter"], item) != 0:
            raise ValueError(f"非喷雾道具{item}意外有喷雾步数")
    for steps, expected, expired in (
        (0, 0, 0),
        (1, 0, 1),
        (2, 1, 0),
        (100, 99, 0),
        (200, 199, 0),
        (250, 249, 0),
    ):
        isolated = RomCPU(rom, state)
        isolated.call(0x0806E584, layout["variable_id"], steps)
        result = isolated.call(layout["decrement_entry"])
        after = isolated.call(0x0806E568, layout["variable_id"])
        if (after, result) != (expected, expired):
            raise ValueError(f"喷雾{steps}步递减/到期路径不符")
        patch = prepare_repel_steps_patch(memory, sha, steps)
        if patch.address != layout["address"] or patch.before != raw:
            raise ValueError("喷雾补丁范围或前值错误")
    section_ptr = memory.r32(layout["save_section_source_pointer"])
    if section_ptr + layout["save_section_offset"] != layout["address"]:
        raise ValueError("喷雾存档节源地址不一致")
    sectors = []
    for sector in range(min(32, len(save) // 0x1000)):
        base = sector * 0x1000
        if struct.unpack_from("<H", save, base + 0xFF4)[0] != layout["save_section"]:
            continue
        if save[base + layout["save_section_offset"] : base + layout["save_section_offset"] + 2] == raw:
            sectors.append(base)
    if not sectors:
        raise ValueError(".sav中section2喷雾变量与即时存档不一致")
    return {
        "passed": True,
        "rom_sha256": sha,
        "variable_id": hex(layout["variable_id"]),
        "address": hex(layout["address"]),
        "width": layout["width"],
        "sample_value": value,
        "edit_range": [layout["edit_min"], layout["edit_max"]],
        "decrement_entry": hex(layout["decrement_entry"]),
        "decrement_hook": hex(hook),
        "decrement_cases": 6,
        "item_steps": layout["verified_items"],
        "save_section_source": hex(section_ptr),
        "save_section": 2,
        "save_section_offset": hex(layout["save_section_offset"]),
        "matching_save_sectors": [hex(offset) for offset in sectors],
        "scope": "Actual ROM VarGet/Set, item parameter and step/expiry routines in isolated CPU, plus current state/save correspondence; live messages and save/reload remain untested.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("save", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    state = read_state(args.state)
    save = args.save.read_bytes()
    result = {
        "box_names": verify_box_names(rom, state, save),
        "repel_steps": verify_repel(rom, state, save),
    }
    result["state_sha256"] = hashlib.sha256(args.state.read_bytes()).hexdigest()
    result["save_sha256"] = hashlib.sha256(args.save.read_bytes()).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
