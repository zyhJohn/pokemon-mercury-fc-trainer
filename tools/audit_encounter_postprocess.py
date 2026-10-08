"""Bounded audit of encounter-origin postprocessing in two exact Mercury FC ROMs.

Only ROM instructions run in isolated Unicorn RAM; ROM and state inputs are read-only.
The synthetic hidden-bit cases test sensitivity, not naturally observed encounters.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rom_versions import RELEASES, load_profile
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU

BASE = 0x08000000
PARTY = 0x02024284
ENEMY = 0x0202402C
COUNT = 0x02024029
RECORD = 0x02001000
STACK = 0x03007E00
SOURCE_CONSTANT = 0x081E9F10
GIFT_IMPL = {"1.0": 0x09D07CAC, "1.2": 0x09D0A130}
HATCH_REBUILD = {"1.0": 0x09D19654, "1.2": 0x09D1BBB8}


def half(rom, address):
    return struct.unpack_from("<H", rom, address - BASE)[0]


def word(rom, address):
    return struct.unpack_from("<I", rom, address - BASE)[0]


def bl_target(rom, address):
    a, b = half(rom, address), half(rom, address + 2)
    if a & 0xF800 != 0xF000 or b & 0xF800 != 0xF800:
        raise AssertionError(f"{address:#x}: not a Thumb BL")
    delta = ((a & 0x7FF) << 12) | ((b & 0x7FF) << 1)
    if delta & 0x400000:
        delta -= 0x800000
    return address + 4 + delta


def assert_call(rom, site, target):
    found = bl_target(rom, site)
    if found != target:
        raise AssertionError(f"{site:#x}: BL to {found:#x}, expected {target:#x}")


def source(raw, packed=False):
    index = 52 if packed else 70
    return (raw[index] >> 7) | ((raw[index + 1] & 7) << 1)


def with_source12(raw):
    result = bytearray(raw)
    result[71] |= 4
    if source(result) != 12:
        raise AssertionError("synthetic hidden bit did not yield source 12")
    return bytes(result)


def static_evidence(rom, version):
    if rom[SOURCE_CONSTANT - BASE] != 4:
        raise AssertionError("CreateMon source constant changed")
    calls = {
        0x0802D824: 0x08040B14,  # capture success -> GiveMonToPlayer
        0x080A016C: 0x08040B14,  # gift -> GiveMonToPlayer
        0x080A01D8: 0x08040B14,  # gift alternate -> GiveMonToPlayer
        0x0806C030: 0x080A011C,  # script gift dispatcher
        0x08053D72: 0x08053B48,  # fixed trade template
        0x08053DCE: 0x0805080C,  # final party/enemy swap
        0x0806D704: 0x080463B8,  # egg step detector
        0x08046E26: 0x08046D60,  # hatch selected party member
        0x08046D7C: 0x08046BFC,  # hatch record rebuild bridge
        0x08046C82: 0x0803FBE8,  # rebuild gets field 37
        0x08046D14: 0x0804037C,  # rebuild sets field 37
        0x081379AC: 0x0803FBE8,  # provenance consumer A
        0x081379FE: 0x0803FBE8,  # provenance consumer B
    }
    for site, target in calls.items():
        assert_call(rom, site, target)
    if half(rom, 0x08046C80) != 0x2125 or half(rom, 0x08046D10) != 0x2125:
        raise AssertionError("hatch rebuild field 37 operands changed")
    if half(rom, 0x081379AA) != 0x2125 or half(rom, 0x081379FC) != 0x2125:
        raise AssertionError("field 37 consumer operands changed")
    if half(rom, 0x081379B8) != 0x0E00 or half(rom, 0x081379BA) != 0x2801:
        raise AssertionError("first provenance consumer code-4/5 comparison changed")
    if word(rom, 0x08046C00) != HATCH_REBUILD[version] | 1:
        raise AssertionError("hatch rebuild bridge changed")
    # The ROM's special table, together with the selected hatch script's
    # literal 25 C2 00, connects the event to the actual special implementation.
    if word(rom, 0x08160064) != 0x08046E21 or word(rom, 0x08160068) != 0x08046FD5:
        raise AssertionError("hatch special 0xC1/0xC2 table entries changed")
    if word(rom, 0x08160154) != 0x08053D69:
        raise AssertionError("trade special 0xFD table entry changed")
    if rom[0x08AA59BA - BASE : 0x08AA59BD - BASE] != b"\x25\xC2\x00":
        raise AssertionError("hatch script special 0xC2 changed")
    if rom[0x081BF546 - BASE : 0x081BF54B - BASE] != b"\x05\xAC\x59\xAA\x08":
        raise AssertionError("step hatch script changed")
    return {
        "capture_to_party_or_pc": "0x0802d824 -> 0x08040b14",
        "gift_to_party_or_pc": ["0x080a016c", "0x080a01d8"],
        "trade_constructor_to_swap": ["0x08053d72", "0x08053dce"],
        "hatch_rebuild_bridge": f"{HATCH_REBUILD[version]:#010x}",
        "hatch_field37_get_set": ["0x08046c82", "0x08046d14"],
        "hatch_special_table_c2": "0x08160068 -> 0x08046fd5",
        "hatch_special_table_c1": "0x08160064 -> 0x08046e21",
        "field37_code4_5_consumers": ["0x081379ac", "0x081379fe"],
        "asserted_bl_sites": len(calls),
    }


def new_cpu(rom, state):
    return RomCPU(rom, state, timeout_us=1_000_000)


def create_plain(cpu):
    cpu.write(RECORD, bytes(100))
    cpu.write(STACK, struct.pack("<IIII", 1, 0, 0, 0))
    cpu.call(0x0803DA54, RECORD, 1, 5, 0)
    result = cpu.read(RECORD, 100)
    if source(result) != 4:
        raise AssertionError("plain constructor source changed")
    return result


def create_egg(cpu):
    cpu.write(RECORD, bytes(100))
    cpu.call(0x08046150, RECORD, 1, 1)
    egg = cpu.read(RECORD, 100)
    if source(egg) != 4 or cpu.call(0x0803FBE8, RECORD, 45) != 1:
        raise AssertionError("egg fixture not created")
    return egg


def changed_pc_slot(cpu, addresses, before):
    changed = []
    for box, address in enumerate(addresses):
        after = cpu.read(address, 30 * 58)
        for slot in range(30):
            lo = slot * 58
            if after[lo : lo + 58] != before[box][lo : lo + 58]:
                changed.append(after[lo : lo + 58])
    if len(changed) != 1:
        raise AssertionError(f"expected one changed PC slot, found {len(changed)}")
    return changed[0]


def insertion_case(rom, state, profile, code, full):
    cpu = new_cpu(rom, state)
    raw = create_plain(cpu)
    if code == 12:
        raw = with_source12(raw)
    cpu.write(COUNT, bytes((6 if full else 0,)))
    cpu.write(PARTY, raw * 6 if full else bytes(600))
    addresses = profile["storage"]["box_addresses"]
    before = [cpu.read(a, 30 * 58) for a in addresses]
    cpu.write(RECORD, raw)
    returned = cpu.call(0x08040B14, RECORD)
    if full:
        placed = changed_pc_slot(cpu, addresses, before)
        if returned != 1 or cpu.read(COUNT, 1) != b"\x06" or source(placed, True) != code:
            raise AssertionError("full-party PC insertion changed source")
        return "pc"
    if returned != 0 or cpu.read(COUNT, 1) != b"\x01" or cpu.read(PARTY, 100) != raw:
        raise AssertionError("empty-party insertion changed record")
    if any(cpu.read(a, 30 * 58) != old for a, old in zip(addresses, before)):
        raise AssertionError("empty-party insertion unexpectedly changed PC")
    return "party"


def gift_case(rom, state, profile, version, code, full):
    cpu = new_cpu(rom, state)
    existing = create_plain(cpu)
    cpu.write(COUNT, bytes((6 if full else 0,)))
    cpu.write(PARTY, existing * 6 if full else bytes(600))
    addresses = profile["storage"]["box_addresses"]
    before = [cpu.read(a, 30 * 58) for a in addresses]
    seen = []
    site = GIFT_IMPL[version] + 0x40

    def observe(uc, address, _size, _user):
        if address != site:
            return
        pointer = uc.reg_read(UC_ARM_REG_SP) + 0x1C
        raw = bytes(uc.mem_read(pointer, 100))
        if source(raw) != 4:
            raise AssertionError("gift constructor source changed")
        if code == 12:
            raw = with_source12(raw)
            uc.mem_write(pointer, raw)
        seen.append(raw)

    hook = cpu.cpu.hook_add(UC_HOOK_CODE, observe)
    try:
        cpu.write(STACK, struct.pack("<II", 0, 0))
        returned = cpu.call(0x080A011C, 1, 5, 0, 0)
    finally:
        cpu.cpu.hook_del(hook)
    if len(seen) != 1:
        raise AssertionError("gift construction hook did not run exactly once")
    if full:
        placed = changed_pc_slot(cpu, addresses, before)
        if returned != 1 or source(placed, True) != code:
            raise AssertionError("gift PC postprocessing changed source")
        return "pc"
    placed = cpu.read(PARTY, 100)
    if returned != 0 or cpu.read(COUNT, 1) != b"\x01" or source(placed) != code:
        raise AssertionError("gift party postprocessing changed source")
    return "party"


def trade_case(rom, state, code):
    cpu = new_cpu(rom, state)
    outgoing = create_plain(cpu)
    cpu.write(PARTY, outgoing)
    cpu.call(0x08053B48, 0, 0)
    incoming = cpu.read(ENEMY, 100)
    if source(incoming) != 4:
        raise AssertionError("trade template source changed")
    if code == 12:
        incoming = with_source12(incoming)
        cpu.write(ENEMY, incoming)
    cpu.write(0x02031DAC, struct.pack("<I", 0x02001800))
    cpu.call(0x0805080C, 0, 0)
    placed = cpu.read(PARTY, 100)
    changed = [i for i in range(100) if incoming[i] != placed[i]]
    if source(placed) != code or cpu.read(ENEMY, 100) != outgoing or changed != [41]:
        raise AssertionError(f"trade final swap changed unexpected bytes: {changed}")
    return {"source": source(placed), "non_source_changed_byte": changed}


def hatch_case(rom, state, code):
    cpu = new_cpu(rom, state)
    egg = create_egg(cpu)
    if code == 12:
        egg = with_source12(egg)
    cpu.write(PARTY, egg)
    cpu.write(0x020370C0, b"\x00")  # selected party slot
    cpu.call(0x08046E20)
    hatched = cpu.read(PARTY, 100)
    flag = cpu.call(0x0803FBE8, PARTY, 45)
    if flag != 0 or source(hatched) != 4:
        raise AssertionError("hatch did not clear egg flag and reconstruct source 4")
    if code == 12 and hatched[71] & 4:
        raise AssertionError("hatch unexpectedly retained hidden origin bit")
    return {"input": code, "output": source(hatched), "egg_flag": flag}


def audit(rom, state):
    digest = hashlib.sha256(rom).hexdigest()
    if digest not in RELEASES:
        raise ValueError("unknown ROM SHA-256; exact-version audit refused")
    version = RELEASES[digest][0]
    profile = load_profile(rom)
    static = static_evidence(rom, version)
    matrix = {}
    for code in (4, 12):
        for full in (False, True):
            label = f"source{code}_{'full_pc' if full else 'empty_party'}"
            matrix[label] = {
                "capture_shared_insertion": insertion_case(rom, state, profile, code, full),
                "script_gift_final_insertion": gift_case(rom, state, profile, version, code, full),
            }
        matrix[f"source{code}_trade_final_swap"] = trade_case(rom, state, code)
        matrix[f"source{code}_hatch_completion"] = hatch_case(rom, state, code)
    return {
        "rom_sha256": digest,
        "version": version,
        "static": static,
        "isolated_cpu_matrix": matrix,
        "conclusion": "The audited routes do not encode capture/hatch/trade/gift as distinct field-37 values. Hatch completion also drops hidden bit 2 through GetMonData(37)/SetMonData(37). A method menu is unsupported.",
        "limits": "Full live battle capture, trade scene and save/reload were not run; only the bounded ROM call chains and isolated postprocessing above were tested. Synthetic source 12 is a sensitivity fixture, not a naturally observed encounter.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.rom.read_bytes(), read_state(args.state))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
