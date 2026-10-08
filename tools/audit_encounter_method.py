"""Bounded, read-only audit of field 37 in the two exact Mercury FC ROMs.

Runs actual ROM routines in isolated Unicorn RAM. No emulator, save or ROM writes.
The selected call sites are evidence of behavior, not a complete call graph.
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
from rom_versions import RELEASES
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU

ROM_BASE = 0x08000000
RECORD = 0x02001000
DATA = 0x02001400
PACKED = 0x02001800
UNPACKED = 0x02001C00
PARTY = 0x02002000
STACK = 0x03007E00
GET = 0x0803FBE8
SET = 0x0804037C
CREATE = 0x0803DA54
CREATE_EGG = 0x08046150
CREATE_TEMPLATE = 0x0803E0A4
COMPRESS = {"1.0": 0x09D54A94, "1.2": 0x09D58854}
DECOMPRESS = {"1.0": 0x09D54868, "1.2": 0x09D58628}
EXT_CREATE = {"1.0": 0x09D06C7A, "1.2": 0x09D090FE}
EXT_PREDICATE = {"1.0": 0x09D52AF0, "1.2": 0x09D568B0}
BRIDGE_CREATE = {"1.0": 0x09CCE5B4, "1.2": 0x09CCE674}
GIFT_IMPL = {"1.0": 0x09D07CAC, "1.2": 0x09D0A130}


def half(rom, address):
    return struct.unpack_from("<H", rom, address - ROM_BASE)[0]


def word(rom, address):
    return struct.unpack_from("<I", rom, address - ROM_BASE)[0]


def thumb_bl_target(rom, address):
    first, second = half(rom, address), half(rom, address + 2)
    if first & 0xF800 != 0xF000 or second & 0xF800 != 0xF800:
        raise AssertionError(f"{address:#010x}: expected Thumb BL")
    displacement = ((first & 0x7FF) << 12) | ((second & 0x7FF) << 1)
    if displacement & 0x400000:
        displacement -= 0x800000
    return address + 4 + displacement


def assert_call(rom, site, target):
    actual = thumb_bl_target(rom, site)
    if actual != target:
        raise AssertionError(f"{site:#010x}: BL {actual:#010x}, expected {target:#010x}")


def assert_movs_r1(rom, address, value):
    if half(rom, address) != (0x2100 | value):
        raise AssertionError(f"{address:#010x}: expected movs r1, #{value}")


def literal_pointer(rom, address, register):
    opcode = half(rom, address)
    if opcode & 0xF800 != 0x4800 or (opcode >> 8) & 7 != register:
        raise AssertionError(f"{address:#010x}: expected PC-relative ldr r{register}")
    slot = ((address + 4) & ~3) + (opcode & 255) * 4
    return slot, word(rom, slot)


def static_evidence(rom, version):
    # The retained 0803DAC4 constructor jumps via an exact-version bridge.
    bridge = word(rom, 0x0803DAD8)
    if bridge != BRIDGE_CREATE[version] | 1:
        raise AssertionError("CreateMon bridge changed")
    assert_call(rom, BRIDGE_CREATE[version] + 0x12, EXT_CREATE[version])
    ext = EXT_CREATE[version]
    set_source = ext + 0x100  # movs r1,#37; ldr r2,<constant>; call setter
    assert_movs_r1(rom, set_source, 37)
    slot, pointer = literal_pointer(rom, set_source - 2, 2)
    if pointer != 0x081E9F10 or rom[pointer - ROM_BASE] != 4:
        raise AssertionError("CreateMon field 37 source constant changed")

    # A consumer uses code 4 as a predicate, while another branches on
    # codes 4/5 together with met location and another field.
    predicate = EXT_PREDICATE[version]
    assert_movs_r1(rom, predicate + 0xE, 37)
    assert_call(rom, predicate + 0x10, {"1.0": 0x09D547E8, "1.2": 0x09D585A8}[version])
    if half(rom, predicate + 0x18) != 0x3804:  # subs r0,#4
        raise AssertionError("field 37 == 4 predicate changed")
    for site in (0x081379AC, 0x081379FE):
        assert_movs_r1(rom, site - 2, 37)
        assert_call(rom, site, GET)

    # Selected construction paths and a successful ball branch. The latter
    # writes field 38, so it cannot define field 37's event semantics.
    assert_call(rom, 0x08046178, CREATE)
    assert_movs_r1(rom, 0x080461EC, 45)
    assert_call(rom, 0x080461EE, SET)
    assert_call(rom, 0x0803E0C2, CREATE)
    assert_call(rom, 0x0806C030, 0x080A011C)
    assert_call(rom, 0x08053B8E, CREATE)
    assert_call(rom, 0x08053D72, 0x08053B48)
    assert_movs_r1(rom, 0x0802D6EC, 38)
    assert_call(rom, 0x0802D6EE, SET)
    # The field-37 calls in these six blocks are generic byte-from-buffer
    # setters; no per-action enumerated value is established by them.
    generic_setters = (0x08031A42, 0x080373D0, 0x0803BF88,
                       0x080D58F6, 0x080E997A, 0x08157D06)
    for site in generic_setters:
        assert_movs_r1(rom, site - 2, 37)
        assert_call(rom, site, SET)

    return {
        "create_bridge": f"{BRIDGE_CREATE[version]:#010x}",
        "create_field37_setter_site": f"{ext + 0x100:#010x}",
        "create_constant_pointer_slot": f"{slot:#010x}",
        "create_constant_pointer": f"{pointer:#010x}",
        "create_field37_value": 4,
        "code4_consumer": f"{predicate:#010x}",
        "additional_consumer_calls": [f"{site:#010x}" for site in (0x081379AC, 0x081379FE)],
        "egg_create_call": "0x08046178",
        "egg_flag_setter": "0x080461ee",
        "template_create_call": "0x0803e0c2",
        "script_gift_dispatch_call": "0x0806c030",
        "script_gift_constructor": f"{GIFT_IMPL[version]:#010x}",
        "fixed_trade_template_create_call": "0x08053b8e",
        "fixed_trade_template_dispatch_call": "0x08053d72",
        "capture_ball_setter": "0x0802d6ee",
        "generic_field37_setters": [f"{site:#010x}" for site in generic_setters],
    }


def source(cpu):
    return cpu.call(GET, RECORD, 37)


def create_plain(cpu):
    cpu.write(RECORD, bytes(100))
    cpu.write(STACK, struct.pack("<IIII", 1, 0, 0, 0))
    cpu.call(CREATE, RECORD, 1, 5, 0)
    return cpu.read(RECORD, 100)


def create_egg(cpu):
    cpu.write(RECORD, bytes(100))
    cpu.call(CREATE_EGG, RECORD, 1, 1)
    if cpu.call(GET, RECORD, 45) != 1:
        raise AssertionError("egg constructor did not set the egg field")
    if cpu.call(GET, RECORD, 35) != 253 or cpu.call(GET, RECORD, 36) != 0:
        raise AssertionError("egg location/level differed")
    return cpu.read(RECORD, 100)


def create_template(cpu):
    template = bytearray(64)
    struct.pack_into("<H", template, 0, 1)
    template[12] = 5
    struct.pack_into("<I", template, 20, 0x12345678)
    struct.pack_into("<I", template, 28, 0x87654321)
    cpu.write(RECORD, bytes(100))
    cpu.write(DATA, bytes(template))
    cpu.call(CREATE_TEMPLATE, RECORD, DATA)
    if cpu.call(GET, RECORD, 11) != 1:
        raise AssertionError("template construction failed")
    return cpu.read(RECORD, 100)


def create_script_gift(cpu, version):
    captured = []
    site = GIFT_IMPL[version] + 0x40  # after native CreateMon and held-item setup

    def observe(uc, address, _size, _user):
        if address == site:
            pointer = uc.reg_read(UC_ARM_REG_SP) + 0x1C
            captured.append(bytes(uc.mem_read(pointer, 100)))

    hook = cpu.cpu.hook_add(UC_HOOK_CODE, observe)
    try:
        cpu.write(STACK, struct.pack("<II", 0, 0))
        cpu.call(0x080A011C, 1, 5, 0, 0)
    finally:
        cpu.cpu.hook_del(hook)
    if len(captured) != 1 or struct.unpack_from("<H", captured[0], 32)[0] != 1:
        raise AssertionError("script gift construction was not observed")
    return captured[0]


def create_trade_template(cpu):
    cpu.call(0x08053B48, 0, 0)
    raw = cpu.read(0x0202402C, 100)
    cpu.write(RECORD, raw)
    if cpu.call(GET, RECORD, 11) == 0 or cpu.call(GET, RECORD, 35) != 254:
        raise AssertionError("fixed trade-template construction differed")
    return raw


def pc_roundtrip(cpu, version, raw):
    cpu.write(RECORD, raw)
    cpu.call(COMPRESS[version], RECORD, PACKED)
    packed = cpu.read(PACKED, 58)
    cpu.call(DECOMPRESS[version], UNPACKED, PACKED)
    cpu.call(0x0803E774, UNPACKED, PARTY)
    cpu.write(RECORD, cpu.read(PARTY, 100))
    return packed, source(cpu)


def cpu_evidence(rom, state, version):
    cpu = RomCPU(rom, state, timeout_us=1_000_000)
    original = create_plain(cpu)
    if source(cpu) != 4:
        raise AssertionError("ordinary CreateMon did not produce field 37 == 4")
    cases = {"ordinary": original}
    for name, constructor in (("egg", create_egg), ("template", create_template)):
        raw = constructor(cpu)
        if source(cpu) != 4:
            raise AssertionError(f"{name} constructor did not produce field 37 == 4")
        cases[name] = raw
    cases["script_gift_constructor"] = create_script_gift(cpu, version)
    cases["fixed_trade_template"] = create_trade_template(cpu)
    for name in ("script_gift_constructor", "fixed_trade_template"):
        raw = cases[name]
        cpu.write(RECORD, raw)
        if source(cpu) != 4:
            raise AssertionError(f"{name} did not produce field 37 == 4")

    packed_cases = {}
    for name, raw in cases.items():
        packed, actual = pc_roundtrip(cpu, version, raw)
        if actual != 4 or ((packed[52] >> 7) & 1) | ((packed[53] & 3) << 1) != 4:
            raise AssertionError(f"{name} source code lost in PC compression")
        packed_cases[name] = actual

    # The capture path's field-38 setter changes the ball while preserving
    # the three field-37 bits on a separately constructed candidate.
    cpu.write(RECORD, original)
    cpu.write(DATA, b"\x04")
    cpu.call(SET, RECORD, 38, DATA)
    if source(cpu) != 4 or cpu.call(GET, RECORD, 38) != 4:
        raise AssertionError("ball setter changed the field-37 code")

    bit_cases = 0
    for value in range(16):
        raw = bytearray(original)
        raw[70] = (raw[70] & 0x7F) | 0x80
        raw[71] = (raw[71] & 3) | 0xFC
        cpu.write(RECORD, bytes(raw))
        cpu.write(DATA, bytes((value,)))
        cpu.call(SET, RECORD, 37, DATA)
        actual = cpu.read(RECORD, 100)
        if source(cpu) != (value & 7):
            raise AssertionError(f"field 37 readback {value} failed")
        if actual[70] & 0x7F != raw[70] & 0x7F or actual[71] & 0xF8 != raw[71] & 0xF8:
            raise AssertionError("field 37 setter damaged adjacent packed bits")
        if (actual[70] >> 7) | ((actual[71] & 7) << 1) != value:
            raise AssertionError("field 37 setter did not store the full 4-bit value")
        packed, restored = pc_roundtrip(cpu, version, actual)
        packed_value = ((packed[52] >> 7) & 1) | ((packed[53] & 7) << 1)
        restored_value = (cpu.read(RECORD + 70, 1)[0] >> 7) | ((cpu.read(RECORD + 71, 1)[0] & 7) << 1)
        if restored != (value & 7) or packed_value != value or restored_value != value:
            raise AssertionError(f"field 37 PC roundtrip {value} failed")
        bit_cases += 1

    # This concrete consumer obtains a record from a SaveBlock pointer. The
    # state snapshot leaves that pointer unset, so install a temporary WRAM
    # fixture. The routine is still the unchanged ROM implementation.
    cpu.write(0x0203B140, struct.pack("<I", 0x02000000))
    predicate_cases = 0
    for value in range(16):
        raw = bytearray(100)
        raw[70] = (value & 1) << 7
        raw[71] = (value >> 1) & 7
        cpu.write(0x02003290, bytes(raw))
        actual = cpu.call(EXT_PREDICATE[version])
        if actual != int((value & 7) == 4):
            raise AssertionError(f"field 37 consumer case {value} failed")
        predicate_cases += 1

    return {
        "constructor_field37": {name: 4 for name in cases},
        "egg_flag": 1,
        "egg_location": 253,
        "egg_met_level": 0,
        "pc_constructor_roundtrips": packed_cases,
        "ball_setter_preserved_field37": True,
        "four_bit_storage_values_with_neighbor_and_pc_assertions": bit_cases,
        "getter_alias_pairs": 8,
        "code4_consumer_cases": predicate_cases,
    }


def audit(rom, state):
    digest = hashlib.sha256(rom).hexdigest()
    if digest not in RELEASES:
        raise ValueError("unknown ROM SHA-256; exact-version audit refused")
    version = RELEASES[digest][0]
    return {
        "rom_sha256": digest,
        "version": version,
        "static": static_evidence(rom, version),
        "isolated_cpu": cpu_evidence(rom, state, version),
        "conclusion": "field 37 stores four bits, but GetMonData returns only three. Code 4 occurs in ordinary, egg, template, script-gift and fixed trade-template construction. It does not independently encode capture/hatch/trade/gift method.",
        "unresolved": "Full trade/gift transfer, custom scripts, live capture/hatch and save/reload paths are not exhaustively traced. Do not expose a method menu from this evidence.",
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
