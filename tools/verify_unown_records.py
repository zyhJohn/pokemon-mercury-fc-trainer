"""Read-only ROM evidence; all writes are to an isolated Unicorn RAM image.

python tools/verify_unown_records.py --rom GAME.gba --state GAME.ss1 --save GAME.sav
Rendering/task/audio calls are stubbed only for the notebook dispatch matrix;
FlagGet/Set/Clear, capture instructions and save serialization run unmodified.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_LR, UC_ARM_REG_PC
from pokemon_data import unown_form
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU
from unown_records import (CAPTURE_ADDRESS, CAPTURE_BYTES, FINAL_ADDRESS, LAYOUT,
                           RESEARCH_ADDRESS, flag_address, prepare_unown_records_patches,
                           read_unown_records)


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def trace_reward_branch(rom, enabled_flags):
    """Interpret this bounded native NPC script's control/flag commands only.

    Text/audio standard calls are skipped; stop before any item/reward command.
    No game state or reward is written. Every branch comes from actual ROM bytes.
    """
    flags = set(enabled_flags)
    address = 0x08EC8125
    condition = False
    checked = []
    for _ in range(300):
        if address == 0x08EC85B5:
            return {"reward_reachable": True, "checks": checked}
        if not 0x08EC8100 <= address < 0x08EC8800:
            return {"reward_reachable": False, "checks": checked, "stop": hex(address)}
        offset = address - 0x08000000
        command = rom[offset]
        if command == 0x2B:  # checkflag
            flag = struct.unpack_from("<H", rom, offset + 1)[0]
            checked.append(flag)
            condition = flag in flags
            address += 3
        elif command == 0x29:  # setflag (local script state only)
            flags.add(struct.unpack_from("<H", rom, offset + 1)[0])
            address += 3
        elif command == 0x06:  # goto_if EQ / NEQ
            expected = rom[offset + 1]
            _check(expected in (0, 1), "NPC脚本出现未支持条件")
            address = struct.unpack_from("<I", rom, offset + 2)[0] if condition == bool(expected) else address + 6
        elif command == 0x05:
            address = struct.unpack_from("<I", rom, offset + 1)[0]
        elif command == 0x02:
            return {"reward_reachable": False, "checks": checked, "stop": hex(address)}
        elif command in (0x0F, 0x09, 0x67, 0x66, 0x31, 0x32, 0x6A):
            address += {0x0F: 6, 0x09: 2, 0x67: 5, 0x66: 1, 0x31: 3, 0x32: 1, 0x6A: 1}[command]
        else:
            raise ValueError(f"NPC分支未覆盖指令{command:02X} @{address:08X}")
    raise ValueError("NPC分支未在有界步数内结束")


def verify(rom, state, save=None):
    sha = hashlib.sha256(rom).hexdigest()
    release = LAYOUT["releases"].get(sha)
    _check(release is not None, "ROM SHA-256未核验")
    for feature in release["runtime_features"]:
        start = feature["address"] - 0x08000000
        expected = bytes.fromhex(feature["hex"])
        _check(rom[start:start + len(expected)] == expected, "运行ROM字节守卫与原件不同")
    _check(hashlib.sha256(rom[0xEC83C6:0xEC8642]).hexdigest() == release["npc_script_sha256"],
           "NPC领奖脚本身份不同")
    engine = RomCPU(rom, state)
    snapshot = read_unown_records(engine, sha)
    _check(struct.unpack_from("<I", rom, 0x7C7E00 + 740 * 44 + 28)[0] == release["item_use"] | 1,
           "笔记道具740使用函数不符")
    checks = {"snapshot_flag_get": 0, "individual_flag_writes": 0, "capture_segments": 0,
              "counter_cases": 0, "serialization_cases": 0, "page_dispatch_cases": 0,
              "reward_branch_cases": 0}
    for field in snapshot["fields"]:
        _check(engine.call(release["get_flag_address"], field["flag"]) == flag_address(field["flag"]), "旗标地址不符")
        _check(bool(engine.call(release["flag_get"], field["flag"])) == field["value"], "旗标解码不符")
        checks["snapshot_flag_get"] += 1
    # Compare pure patches with actual FlagSet/Clear on every editable field.
    for field in snapshot["fields"]:
        if not field["editable"]:
            continue
        for value in (False, True):
            engine.write(CAPTURE_ADDRESS, bytes.fromhex("a55a3cc3"))
            engine.write(RESEARCH_ADDRESS, b"\xd2")
            before = read_unown_records(engine, sha)
            patches = prepare_unown_records_patches(before, {field["key"]: value})
            expected = dict(before["records"])
            for patch in patches:
                expected[patch.address] = patch.after
            engine.call(release["flag_set"] if value else release["flag_clear"], field["flag"])
            _check(all(engine.read(address, len(raw)) == raw for address, raw in expected.items()),
                   "ROM位写入与纯补丁不同，或修改了邻位")
            checks["individual_flag_writes"] += 1

    # Execute the actual capture-site species/PID -> form -> FlagSet sequence.
    pids = {}
    for code in range(256):
        pid = sum(((code >> (index * 2)) & 3) << (index * 8) for index in range(4))
        pids.setdefault(unown_form(pid), pid)
        if len(pids) == 28:
            break
    _check(len(pids) == 28, "未生成28字形测试PID")
    def stop_capture(cpu, address, size, data):
        if address == release["capture_end"]:
            cpu.reg_write(UC_ARM_REG_PC, 0x02001100)
    hook = engine.cpu.hook_add(UC_HOOK_CODE, stop_capture)
    try:
        for species in (201, 25):
            for form, pid in sorted(pids.items()):
                engine.write(CAPTURE_ADDRESS, bytes.fromhex("030000c0"))
                engine.cpu.reg_write(UC_ARM_REG_R4, species)
                engine.call(release["capture_start"], pid)
                expected = (int.from_bytes(bytes.fromhex("030000c0"), "little") |
                            ((1 << (form + 2)) if species == 201 else 0)).to_bytes(4, "little")
                _check(engine.read(CAPTURE_ADDRESS, 4) == expected, "捕获实际指令序列未写入唯一对应字形")
                checks["capture_segments"] += 1
    finally:
        engine.cpu.hook_del(hook)

    # The game's counter includes neighbouring flag1706; editor preserves it.
    for bits in (0, 1, 0x5555555, (1 << 28) - 1):
        for extra in (0, 1):
            engine.write(CAPTURE_ADDRESS, ((bits << 2) | (extra << 30) | 3).to_bytes(4, "little"))
            engine.call(release["form_counter"])
            actual = engine.call(0x0806E568, 0x519B)
            _check(actual == bits.bit_count() + extra, "笔记实际计数函数与28字形+相邻位不一致")
            checks["counter_cases"] += 1

    # Original serializer copies the extended fields to section0/4 tail regions.
    source_original = {}
    for mapping in LAYOUT["save_mapping"]:
        source = mapping["ram"]
        size = mapping["size"]
        source_original[source] = engine.read(source, size)
        payload = bytes((index * 37 + mapping["section"]) & 255 for index in range(size))
        engine.write(source, payload)
        engine.write(0x02010000, b"\xA5" * 0x1000)
        engine.write(0x02010FF4, struct.pack("<H", mapping["section"]))
        engine.write(0x03005394, struct.pack("<I", 0x02010000))
        engine.call(release["serializer"])
        _check(engine.read(0x02010000 + mapping["offset"], size) == payload, "实际存档序列化尾区不符")
        _check(engine.read(0x02010000 + mapping["offset"] - 1, 1) == b"\xA5", "序列化越界写前邻字节")
        checks["serialization_cases"] += 1
    for address, raw in source_original.items():
        engine.write(address, raw)

    # Actual notebook branch code, with graphics/audio/task functions stubbed.
    # Page numbers are Var519C zero-based. Record all real FlagGet call sites.
    page_engine = RomCPU(rom, state)
    dispatch = release["notebook_dispatch"]
    dispatch_end = release["notebook_dispatch_end"]
    trampoline = release["trampoline"]
    page_engine.write(0x030030F0 + 0x2E, b"\x10\x00")
    observed = {}
    page = 0
    active = set()
    flags_read = []
    def dispatch_hook(cpu, address, size, data):
        if address == 0x02001100 or dispatch <= address < dispatch_end or trampoline <= address < trampoline + 12:
            return
        value = 0
        argument = cpu.reg_read(UC_ARM_REG_R0)
        if address == 0x0806E568:
            value = page if argument == 0x519C else 0
        elif address == release["flag_get"]:
            flags_read.append(argument)
            value = int(argument in active)
        cpu.reg_write(UC_ARM_REG_R0, value)
        cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))
    hook = page_engine.cpu.hook_add(UC_HOOK_CODE, dispatch_hook)
    try:
        notes = [note["flag"] for note in LAYOUT["notes"] if note["pages"]]
        for page in range(4, 14):
            observed[page] = set()
            for pattern in range(1 << len(notes)):
                active = {flag for index, flag in enumerate(notes) if pattern & (1 << index)}
                flags_read = []
                page_engine.call(dispatch, 0)
                observed[page].update(flags_read)
                checks["page_dispatch_cases"] += 1
    finally:
        page_engine.cpu.hook_del(hook)
    expected_pages = {4: {0xAE0}, 5: {0xAE0, 0xAE2}, 6: {0xAE2}, 7: {0xAE3},
                      8: {0xAE3}, 9: {0xAE5}, 10: {0xAE5}, 11: {0x1752}, 12: {0x1752}, 13: {0x1752}}
    _check(observed == expected_pages, f"笔记实际页面依赖不同：{observed}")

    # Prove required NPC path flags, including experience records AE1/AE4.
    required = {0x1751, 0xAE0, 0xAE1, 0xAE2, 0xAE3, 0xAE4, 0xAE5, *range(0x16EA, 0x1706)}
    _check(trace_reward_branch(rom, required)["reward_reachable"], "完整记录无法进入NPC原生奖励链")
    checks["reward_branch_cases"] += 1
    for missing in sorted(required):
        _check(not trace_reward_branch(rom, required - {missing})["reward_reachable"],
               f"漏掉必要条件{missing:04X}仍能进入奖励链")
        checks["reward_branch_cases"] += 1
    _check(not trace_reward_branch(rom, required | {0x1752})["reward_reachable"], "已完成任务仍能重进奖励链")
    checks["reward_branch_cases"] += 1
    _check(rom[0xEC85B5:0xEC85BF] == bytes.fromhex("1a008062011a01800100"), "秘密琥珀奖励命令不符")
    _check(rom[0xEC861E:0xEC8621] == bytes.fromhex("295217"), "奖励后完成旗标不符")

    save_matches = []
    if save is not None:
        # Report paired-save observation independently from isolated serialization.
        fresh = RomCPU(rom, state)
        for sector in range(min(32, len(save) // 0x1000)):
            base = sector * 0x1000
            section = struct.unpack_from("<H", save, base + 0xFF4)[0]
            if struct.unpack_from("<I", save, base + 0xFF8)[0] != 0x08012025:
                continue
            for mapping in LAYOUT["save_mapping"]:
                if section == mapping["section"]:
                    raw = fresh.read(mapping["ram"], mapping["size"])
                    matches = save[base + mapping["offset"]:base + mapping["offset"] + len(raw)] == raw
                    save_matches.append({"sector": sector, "section": section, "entire_extended_region_matches": matches})
    return {"passed": True, "rom_sha256": sha, "version": release["version"], "checks": checks,
            "runtime_rom_guard_count": len(release["runtime_features"]),
            "runtime_rom_guard_bytes": sum(len(bytes.fromhex(feature["hex"])) for feature in release["runtime_features"]),
            "page_flag_dependencies": {str(key): [hex(flag) for flag in sorted(flags)] for key, flags in observed.items()},
            "npc_reward_requirements": [hex(flag) for flag in sorted(required)],
            "paired_save_observations": save_matches,
            "scope": "实际旗标读写、捕获指令片段、计数、序列化；页面分支使用图形/任务/声音stub。未验证实机菜单/游戏内保存重载。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.rom.read_bytes(), read_state(args.state), args.save.read_bytes() if args.save else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.report:
        target = args.report.resolve()
        if not target.is_relative_to((ROOT / "diagnostics").resolve()):
            raise ValueError("报告只可写入已忽略的diagnostics目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
