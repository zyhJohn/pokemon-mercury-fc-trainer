"""Bounded, read-only audit of daycare paths in the two exact Mercury ROMs.

The input ROMs, mGBA states, and Flash saves are never written. Unicorn runs
the ROM routines against private copies of state RAM. This is research code,
not a trainer edit or a replacement for game/save-reload acceptance.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rom_versions import RELEASES
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU
from trainer_core import PARTY, PARTY_COUNT


SAVE1_POINTER = 0x03005008
DAYCARE_OFFSET = 0x2F80
PARENT_SIZE = 0x8C
PARENTS_SIZE = 0x118
PENDING_FLAG = 0x266
FLASH_SIGNATURE = 0x08012025
HOOKS = {
    "trigger": 0x080459F0,
    "give": 0x080460D4,
    "pending": 0x080463FC,
    "compatibility": 0x0804654C,
}
TARGETS = {
    "1.0": {"trigger": 0x09D1962C, "give": 0x09D18E4C,
            "pending": 0x09D19640, "compatibility": 0x09D1989C},
    "1.2": {"trigger": 0x09D1BB90, "give": 0x09D1B3B0,
            "pending": 0x09D1BBA4, "compatibility": 0x09D1BE00},
}


def u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def rom_at(rom, address, length):
    offset = address - 0x08000000
    if not 0 <= offset <= len(rom) - length:
        raise ValueError(f"ROM address out of bounds: {address:08X}")
    return rom[offset:offset + length]


def thumb_bl_target(rom, address):
    hi, lo = struct.unpack("<HH", rom_at(rom, address, 4))
    if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
        raise ValueError(f"expected Thumb BL at {address:08X}")
    displacement = ((hi & 0x7FF) << 11) | (lo & 0x7FF)
    if displacement & 0x200000:
        displacement -= 0x400000
    return address + 4 + 2 * displacement


def verify_static(rom, version):
    targets = TARGETS[version]
    for name, hook in HOOKS.items():
        # Each entry is an exact Thumb LDR + BX trampoline with a literal target.
        if rom_at(rom, hook, 4) not in (b"\x00\x49\x08\x47", b"\x00\x4a\x10\x47"):
            raise ValueError(f"{version} {name}: trampoline changed")
        if u32(rom_at(rom, hook + 4, 4), 0) != targets[name] | 1:
            raise ValueError(f"{version} {name}: target changed")
    trigger = targets["trigger"]
    pending = targets["pending"]
    # The two actual wrappers load flag 0x266 and call FlagSet/FlagGet.
    for entry, flag_fn in ((trigger, 0x0806E680), (pending, 0x0806E6D0)):
        if u32(rom_at(rom, entry + 12, 4), 0) != PENDING_FLAG:
            raise ValueError("pending flag literal changed")
        if u32(rom_at(rom, entry + 16, 4), 0) != flag_fn | 1:
            raise ValueError("pending flag call target changed")
    # Actual overworld routine: parent species test, accumulated step increment,
    # two-parent count and +0x118 token/+0x114 low-byte gates, compatibility call.
    expected_calls = {
        0x080462D8: 0x0803FD44,  # GetBoxMonData field 5
        0x08046310: HOOKS["compatibility"],
        0x08046330: 0x08045A48,  # wrapper to TriggerPendingDaycareEgg
    }
    for callsite, target in expected_calls.items():
        if thumb_bl_target(rom, callsite) != target:
            raise ValueError(f"daycare step call changed at {callsite:08X}")
    if thumb_bl_target(rom, 0x08045A54) != HOOKS["trigger"]:
        raise ValueError("step trigger wrapper changed")
    if thumb_bl_target(rom, 0x080462B8) != HOOKS["give"]:
        raise ValueError("daycare gift wrapper changed")
    return {name: f"0x{target:08X}" for name, target in targets.items()}


def flash_sections(save):
    """Select latest valid CFRU Flash sections needed by this audit."""
    if len(save) < 0x20000:
        raise ValueError("Flash save too short")
    result = {}
    sizes = {1: 0xFF0, 3: 0xFF0, 4: 0xD98}
    for sector_index in range(32):
        sector = save[sector_index * 0x1000:(sector_index + 1) * 0x1000]
        section_id, checksum, signature, counter = struct.unpack_from("<HHII", sector, 0xFF4)
        if signature != FLASH_SIGNATURE or section_id not in sizes:
            continue
        size = sizes[section_id]
        total = sum(struct.unpack_from(f"<{size // 4}I", sector)) & 0xFFFFFFFF
        expected = ((total >> 16) + total) & 0xFFFF
        if checksum != expected:
            continue
        if section_id not in result or counter > result[section_id]["counter"]:
            result[section_id] = {"counter": counter, "sector": sector,
                                  "sector_index": sector_index}
    if set(result) != set(sizes):
        raise ValueError("missing valid Flash sections 1, 3, or 4")
    if len({value["counter"] for value in result.values()}) != 1:
        raise ValueError("needed Flash sections do not share a save counter")
    return result


def sample_info(rom, state, save):
    memory = StateMemory(state, rom)
    save1 = memory.r32(SAVE1_POINTER)
    if not 0x02000000 <= save1 <= 0x0203C000 - 0x3D68:
        raise ValueError("SaveBlock1 pointer outside expected EWRAM range")
    daycare = save1 + DAYCARE_OFFSET
    engine = RomCPU(rom, state)
    state_species = [engine.call(0x0803FD44, daycare + i * PARENT_SIZE, 11)
                     for i in range(2)]
    sections = flash_sections(save)
    # CFRU copies SaveBlock1 in 0xFF0-byte chunks. The daycare straddles the
    # end of section 3 and start of section 4; flag 0x266 is in section 1.
    saved_daycare = sections[3]["sector"][0xFA0:0xFF0] + sections[4]["sector"][:0xCB]
    if len(saved_daycare) != 0x11B:
        raise AssertionError("daycare Flash span length")
    saved_engine = RomCPU(rom, state)
    saved_engine.write(daycare, saved_daycare)
    saved_species = [saved_engine.call(0x0803FD44, daycare + i * PARENT_SIZE, 11)
                     for i in range(2)]
    return {
        "daycare_address": f"0x{daycare:08X}",
        "state_parent_species": state_species,
        "state_parent_steps": [memory.r32(daycare + i * PARENT_SIZE + 0x88)
                               for i in range(2)],
        "state_compatibility": engine.call(HOOKS["compatibility"], daycare),
        "state_pending": engine.call(HOOKS["pending"]),
        "state_offspring_token": u16(memory.read(daycare + 0x118, 2), 0),
        "state_step_counter": memory.r8(daycare + 0x11A),
        "saved_parent_species": saved_species,
        "saved_pending": bool(sections[1]["sector"][0xF2C] & 0x40),
        "saved_offspring_token": u16(saved_daycare, 0x118),
        "saved_step_counter": saved_daycare[0x11A],
        "save_counter": sections[1]["counter"],
    }, memory.read(daycare, PARENTS_SIZE)


def run_matrix(rom, state, donor, version):
    memory = StateMemory(state, rom)
    daycare = memory.r32(SAVE1_POINTER) + DAYCARE_OFFSET
    cases = []
    scenarios = [
        ("no_parents", bytes(PARENTS_SIZE), 0xFE, 0, 0, 0),
        ("one_parent", donor[:PARENT_SIZE] + bytes(PARENT_SIZE), 0xFE, 0, 0, 0),
        ("incompatible_same_parent", donor[:PARENT_SIZE] * 2, 0xFE, 0, 0, 0),
        ("step_not_due", donor, 0xFD, 0, 0, 20),
        ("offspring_token_set", donor, 0xFE, 1, 0, 20),
        ("compatible_rng_miss", donor, 0xFE, 0, 1, 20),
        ("compatible_rng_hit", donor, 0xFE, 0, 0, 20),
    ]
    for label, parents, step_low, token, seed, wanted_score in scenarios:
        engine = RomCPU(rom, state)
        engine.write(daycare, parents)
        engine.write(daycare + PARENT_SIZE + 0x88, struct.pack("<I", step_low))
        engine.write(daycare + 0x118, struct.pack("<H", token) + b"\0")
        engine.write(0x03005000, struct.pack("<I", seed))
        engine.call(0x0806E6A8, PENDING_FLAG)
        score = engine.call(HOOKS["compatibility"], daycare)
        if score != wanted_score:
            raise ValueError(f"{version} {label}: compatibility {score}")
        before_party = engine.read(PARTY, 600)
        engine.call(0x080462C4, daycare)
        pending = engine.call(HOOKS["pending"])
        expected = int(label == "compatible_rng_hit")
        if pending != expected:
            raise ValueError(f"{version} {label}: pending {pending}, expected {expected}")
        if engine.read(PARTY, 600) != before_party:
            raise ValueError(f"{version} {label}: step produced a party egg")
        cases.append({"case": label, "score": score, "pending_after_step": pending})
    # The trigger itself has no parent/compatibility guard. Prove that failure
    # on private RAM so nobody uses it as the UI precondition.
    engine = RomCPU(rom, state)
    engine.write(daycare, bytes(PARENTS_SIZE))
    engine.call(0x0806E6A8, PENDING_FLAG)
    before = engine.read(memory.r32(SAVE1_POINTER), 0x3D68)
    engine.call(HOOKS["trigger"], daycare)
    if engine.call(HOOKS["pending"]) != 1:
        raise ValueError("unguarded trigger behavior changed")
    after = engine.read(memory.r32(SAVE1_POINTER), 0x3D68)
    changes = [i for i, (old, new) in enumerate(zip(before, after)) if old != new]
    if changes != [0xF2C] or before[0xF2C] & 0x40 or not after[0xF2C] & 0x40:
        raise ValueError(f"{version}: pending trigger changed unexpected SaveBlock1 bytes")
    return cases


def verify_give(rom, state, donor, version):
    engine = RomCPU(rom, state)
    daycare = StateMemory(state, rom).r32(SAVE1_POINTER) + DAYCARE_OFFSET
    engine.write(daycare, donor)
    engine.call(0x0806E6A8, PENDING_FLAG)
    engine.write(daycare + PARENT_SIZE + 0x88, struct.pack("<I", 0xFE))
    engine.write(daycare + 0x118, b"\0\0\0")
    engine.write(0x03005000, struct.pack("<I", 0))
    engine.call(0x080462C4, daycare)
    if engine.call(HOOKS["pending"]) != 1:
        raise ValueError("native step did not set pending before gift")
    egg_address = PARTY + 5 * 100
    engine.write(egg_address, bytes(100))
    engine.write(PARTY_COUNT, b"\5")
    engine.call(HOOKS["give"], daycare)
    getter = lambda field: engine.call(0x0803FBE8, egg_address, field)
    if engine.read(PARTY_COUNT, 1) != b"\6" or getter(11) != 173 or getter(45) != 1:
        raise ValueError(f"{version}: native daycare gift did not yield a Cleffa egg")
    parent_ivs = [[engine.call(0x0803FD44, daycare + i * PARENT_SIZE, field)
                   for field in range(39, 45)] for i in range(2)]
    egg_ivs = [getter(field) for field in range(39, 45)]
    inherited = sum(value in (parent_ivs[0][i], parent_ivs[1][i])
                    for i, value in enumerate(egg_ivs))
    if inherited < 3:
        raise ValueError(f"{version}: fewer than three IVs match parents")
    # The ROM's GiveEggFromDaycare leaves pending set. The NPC collection script
    # must clear it separately; this audit does not run the full NPC script.
    return {"egg_species": getter(11), "egg_flag": getter(45),
            "inherited_iv_positions_at_least": inherited,
            "party_count_after": engine.read(PARTY_COUNT, 1)[0],
            "pending_after_give": engine.call(HOOKS["pending"])}


def verify_npc_commands(rom, state, donor):
    """Execute bounded daycare NPC script handlers, not the full dialogue."""
    if rom_at(rom, 0x08167DC9, 8) != bytes.fromhex("2a 66 02 25 b7 00 6c 02"):
        raise ValueError("daycare reject script changed")
    if rom_at(rom, 0x08167E04, 8) != bytes.fromhex("25 b8 00 2a 66 02 6c 02"):
        raise ValueError("daycare receive/clear script changed")
    if rom_at(rom, 0x08167DD1, 15) != bytes.fromhex(
        "26 0d 80 83 00 21 0d 80 06 00 06 05 eb 7d 16"
    ):
        raise ValueError("daycare party-capacity branch changed")

    def command(rom, opcode):
        pointer = u32(rom_at(rom, 0x0815F9B4 + 4 * opcode, 4), 0)
        if not 0x08000001 <= pointer <= 0x09FFFFFF or not pointer & 1:
            raise ValueError("script command handler pointer invalid")
        return pointer & ~1

    memory = StateMemory(state, rom)
    save1 = memory.r32(SAVE1_POINTER)
    daycare = save1 + DAYCARE_OFFSET
    sample_mon = memory.read(PARTY, 100)
    if u16(sample_mon, 32) == 0:
        raise ValueError("sample party member missing for NPC matrix")
    branches = []
    for count in (0, 5, 6):
        engine = RomCPU(rom, state)
        context = 0x02001200
        engine.write(context, bytes(128))
        engine.write(PARTY, sample_mon * count + bytes(100 * (6 - count)))
        engine.write(PARTY_COUNT, b"\0")
        for script_pointer, opcode in (
            (0x08167DD2, 0x26), (0x08167DD7, 0x21), (0x08167DDC, 0x06)
        ):
            engine.write(context + 8, struct.pack("<I", script_pointer))
            engine.call(command(rom, opcode), context)
        destination = u32(engine.read(context + 8, 4), 0)
        expected = 0x08167DE1 if count == 6 else 0x08167DEB
        if destination != expected:
            raise ValueError(f"party count {count}: NPC branch differs")
        branches.append({"party_count": count, "branch": f"0x{destination:08X}"})

    engine = RomCPU(rom, state)
    context = 0x02001200
    engine.write(context, bytes(128))
    engine.write(daycare, donor + b"\0\0\x57")
    before = engine.read(daycare, 0x11B)
    engine.write(PARTY, sample_mon * 5 + bytes(100))
    engine.write(PARTY_COUNT, b"\5")
    engine.call(0x0806E680, PENDING_FLAG)
    engine.write(context + 8, struct.pack("<I", 0x08167E05))
    engine.call(command(rom, 0x25), context)
    if engine.read(PARTY_COUNT, 1) != b"\6" or engine.call(HOOKS["pending"]) != 1:
        raise ValueError("NPC give handler did not add one egg while leaving pending")
    engine.write(context + 8, struct.pack("<I", 0x08167E08))
    engine.call(command(rom, 0x2A), context)
    if engine.call(HOOKS["pending"]) != 0 or engine.read(daycare, 0x11B) != before:
        raise ValueError("NPC clear handler did not clear flag while preserving daycare")
    return {"party_capacity_branches": branches,
            "give_then_clear_flag": True, "daycare_bytes_preserved": 0x11B}


def audit(inputs):
    loaded = {}
    for version, paths in inputs.items():
        rom = paths["rom"].read_bytes()
        sha = hashlib.sha256(rom).hexdigest()
        if sha not in RELEASES or RELEASES[sha][0] != version:
            raise ValueError(f"{version}: exact ROM SHA-256 mismatch")
        state = read_state(paths["state"])
        save = paths["save"].read_bytes()
        static = verify_static(rom, version)
        sample, parents = sample_info(rom, state, save)
        loaded[version] = {"rom": rom, "state": state, "sha": sha,
                           "static": static, "sample": sample, "parents": parents}
    donor = loaded["1.0"]["parents"]
    if loaded["1.0"]["sample"]["state_parent_species"] != [36, 132]:
        raise ValueError("V1.0 sample no longer contains the audited Clefable/Ditto parents")
    results = {}
    for version, item in loaded.items():
        results[version] = {
            "rom_sha256": item["sha"], "static_targets": item["static"],
            "sample": item["sample"],
            "step_matrix": run_matrix(item["rom"], item["state"], donor, version),
            "native_give": verify_give(item["rom"], item["state"], donor, version),
            "npc_handlers": verify_npc_commands(item["rom"], item["state"], donor),
            "synthetic_pair_source": "V1.0 instantaneous state, copied only into private Unicorn RAM",
        }
    return {"passed": True, "releases": results,
            "scope": "Actual ROM step/flag/compatibility/gift and bounded NPC command handlers in isolated RAM; no full dialogue, animation, save/reload, or game transaction."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for version, prefix in (("1.0", "v10"), ("1.2", "v12")):
        for kind in ("rom", "state", "save"):
            parser.add_argument(f"--{prefix}-{kind}", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    inputs = {version: {kind: getattr(args, f"{prefix}_{kind}")
                        for kind in ("rom", "state", "save")}
              for version, prefix in (("1.0", "v10"), ("1.2", "v12"))}
    result = audit(inputs)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
