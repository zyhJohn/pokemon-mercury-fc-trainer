"""Verify Mercury integrity diagnostics with isolated, exact-version ROM code.

Usage: python tools/verify_pokemon_integrity.py ROM STATE --report REPORT.json
Requires the optional Unicorn dependency used by verify_rom_routines.py.
ROM/state inputs are read only. Reports contain aggregate results and identities,
never Pokemon bytes, names, trainer IDs, or input filenames.
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokemon_integrity import inspect_record
from rom_versions import ADDRESS_MAPS, RELEASES, load_profile
from tools.read_state import StateMemory, read_state
from tools.research_report import write_report
from tools.verify_rom_routines import RomCPU

HAS_SPECIES = {"1.0": 0x09D0709C, "1.2": 0x09D09520, "1.33": 0x09D09E14}


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def verify(rom, state, profile):
    """Run ROM matrices and scan the supplied RAM fixture without exporting it.

    The caller's fixture may come from another release. RAM consistency and
    exact ROM identity are checked; the fixture's originating ROM is unproven.
    A snapshot scan counts diagnoses, not an independent encounter-legality
    oracle. Existing anomalies are reported rather than repaired or hidden.
    """
    digest = hashlib.sha256(rom).hexdigest()
    _check(digest in RELEASES, "需要精确匹配已核验版本的 ROM SHA-256")
    _check(profile.get("rom_sha256") == digest, "ROM SHA-256 与配置不匹配")
    version, crc, _ = RELEASES[digest]
    _check(profile.get("rom_crc32") == crc, "ROM CRC 身份与配置不匹配")
    memory = StateMemory(state, rom)
    count = memory.r8(0x02024029)
    _check(count <= 6, "即时存档队伍数量超出已验证范围")
    engine = RomCPU(rom, state)

    # These are constructed records, not an export of personal Pokemon data.
    source = bytearray(100)
    struct.pack_into("<H", source, 32, 25)
    struct.pack_into("<H", source, 44, 33)
    source[84] = 5
    getter_records = getter_calls = 0
    for flags in range(8):
        for egg in (False, True):
            for restored in (0, 25, 65535):
                raw = bytearray(source)
                raw[19] = flags
                struct.pack_into("<H", raw, 28, restored)
                struct.pack_into("<I", raw, 72, int(egg) << 30)
                expected = {
                    4: flags & 1,
                    5: int(not (flags & 1)),
                    6: (flags >> 2) & 1,
                    11: 412 if flags & 1 else 25,
                    45: int(egg),
                    65: 412 if flags & 1 or egg else 25,
                }
                engine.write(0x02001000, bytes(raw))
                for field, value in expected.items():
                    actual = engine.call(0x0803FBE8, 0x02001000, field, 0x02001800)
                    _check(actual == value, f"实际 GetMonData 字段 {field} 与构造矩阵不同")
                    _check(engine.read(0x02001000, 100) == bytes(raw), "GetMonData 改写输入记录")
                    getter_calls += 1
                getter_records += 1

    pc_source = bytearray(58)
    struct.pack_into("<H", pc_source, 28, 25)
    pc_source[39:44] = (33).to_bytes(5, "little")
    pc_cases = 0
    for flags in range(8):
        for egg in (False, True):
            raw = bytearray(pc_source)
            raw[19] = flags
            struct.pack_into("<I", raw, 54, int(egg) << 30)
            engine.write(0x02000800, bytes(raw))
            engine.write(0x02001000, bytes(100))
            engine.call(0x09D54868, 0x02001000, 0x02000800)
            decoded = engine.read(0x02001000, 80)
            _check(engine.read(0x02000800, 58) == bytes(raw), "PC 解压改写输入记录")
            _check(decoded[19] == flags, "PC 解压未原样保留头部标志")
            _check(bool(struct.unpack_from("<I", decoded, 72)[0] & (1 << 30)) == egg,
                   "PC 解压未原样保留 IV 蛋标志")
            pc_cases += 1

    mapped = ADDRESS_MAPS[version]
    pc_table = mapped.get(0x09DD71AC, 0x09DD71AC)
    boxes = struct.unpack_from("<25I", rom, pc_table - 0x08000000)
    party_counts = Counter()
    pc_counts = Counter()
    for slot in range(count):
        raw = memory.read(0x02024284 + 100 * slot, 100)
        party_counts[inspect_record(raw, profile)["status"]] += 1
    for address in boxes:
        for slot in range(30):
            raw = memory.read(address + 58 * slot, 58)
            pc_counts[inspect_record(raw, profile, "box")["status"]] += 1
    statuses = ("empty", "healthy", "bad_egg", "damaged")
    anomalies = sum(counts[status] for counts in (party_counts, pc_counts)
                    for status in ("bad_egg", "damaged"))
    skip_matches = rom[0x3FDA8:0x3FDAA] == bytes.fromhex("0be0")
    nop_matches = rom[0x40AE6:0x40AE8] == bytes.fromhex("c046")
    _check(skip_matches and nop_matches, "校验和跳过/写回 NOP 指令与核验版本不同")
    return {
        "passed": True,
        "rom_version": version,
        "rom_sha256": digest,
        "rom_crc32": crc,
        "state_sha256": hashlib.sha256(state).hexdigest(),
        "fixture_origin_version_verified": False,
        "getter_records": getter_records,
        "getter_calls": getter_calls,
        "pc_decompress_flag_cases": pc_cases,
        "getter_input_unchanged": True,
        "pc_input_unchanged": True,
        "checksum_skip_matches": skip_matches,
        "checksum_write_nop_matches": nop_matches,
        "party_slots_scanned": count,
        "pc_slots_scanned": 750,
        "party_status_counts": {status: party_counts[status] for status in statuses},
        "pc_status_counts": {status: pc_counts[status] for status in statuses},
        "snapshot_anomaly_count": anomalies,
        "snapshot_anomaly_free": anomalies == 0,
        "addresses": {
            "get_mon_data": "0x0803FBE8",
            "get_box_mon_data": "0x0803FD44",
            "has_species": f"0x{HAS_SPECIES[version]:08X}",
            "checksum_skip": "0x0803FDA8",
            "checksum_skip_target": "0x0803FDC2",
            "checksum_write_nop": "0x08040AE6",
            "species_badflag_branch": "0x0804002E",
            "pc_decompress": f"0x{mapped.get(0x09D54868, 0x09D54868):08X}",
            "pc_address_table": f"0x{pc_table:08X}",
        },
        "scope": (
            "Read-only exact-SHA ROM; execution changes only an isolated Unicorn RAM copy. "
            "STATE is supplied by the caller; its originating ROM/version is not independently "
            "verified. A fixture from another version proves routine compatibility only, including "
            "when testing V1.33 with an older state. Snapshot counts describe inspect_record "
            "diagnoses and do not independently establish encounter legality or universal absence "
            "of false positives. No live emulator, menus, save/reload, or save migration tested. "
            "No raw Pokemon records, names, trainer IDs, or input paths are exported."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    _check(hashlib.sha256(rom).hexdigest() in RELEASES,
           "需要精确匹配已核验版本的 ROM SHA-256")
    result = verify(rom, read_state(args.state), load_profile(rom))
    write_report(args.report, result, inputs=(args.rom, args.state))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
