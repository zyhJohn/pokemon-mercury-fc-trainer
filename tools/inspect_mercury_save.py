"""Read-only base-section checks for pinned Mercury research ROMs and saves.

This does not select the newest slot or validate parasite data in section tails.
Native save and fresh battery-only boot evidence must establish persistence of
those extended fields. No runtime compatibility is granted by this tool.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import RELEASES
from tools.research_report import write_report

SPECS = ((0x0000, 0xF24), (0x0000, 0xFF0), (0x0FF0, 0xFF0),
         (0x1FE0, 0xFF0), (0x2FD0, 0xD98), (0x0000, 0xFF0),
         (0x0FF0, 0xFF0), (0x1FE0, 0xFF0), (0x2FD0, 0xFF0),
         (0x3FC0, 0xFF0), (0x4FB0, 0xFF0), (0x5FA0, 0xFF0),
         (0x6F90, 0xFF0), (0x7F80, 0x450))
TABLES = {"1.0": 0x09DD746C, "1.2": 0x09DDEE28, "1.33": 0x09DE4D3C}
AUDITED_ROMS = {sha: record[0] for sha, record in RELEASES.items()}
AUDITED_ROMS.update({
    "093a110d43d9adbd1aa4a8b2fcdc54dff0eebce8a343b8f54354dbdb5d540de3": "1.0",
    "2803552d5e5bb37855080bdef5116fc75f8f8064ddfd7f6b6024dd5a338739d5": "1.2",
    "440441659ee4273a3e0a7af87207a160ee5406deceef2359c991659316bcafb5": "1.33",
})


def checksum(data):
    """Actual 080DA1A8 little-endian u32 sum, u32 wrap, then folded u16."""
    if not isinstance(data, bytes) or not data or len(data) % 4:
        raise ValueError("Base checksum needs nonempty aligned immutable bytes")
    total = sum(word[0] for word in struct.iter_unpack("<I", data)) & 0xFFFFFFFF
    return (total + (total >> 16)) & 0xFFFF


def analyze_save(rom: bytes, save: bytes):
    if not isinstance(rom, bytes) or not isinstance(save, bytes):
        raise TypeError("Research inputs must be immutable bytes")
    rom_sha = hashlib.sha256(rom).hexdigest()
    version = AUDITED_ROMS.get(rom_sha)
    if version is None or len(rom) != 0x02000000:
        raise ValueError("Save research requires an exact audited baseline or safety candidate")
    table = TABLES[version]
    pointer = struct.unpack_from("<I", rom, 0xDA23C)[0]
    expected = b"".join(struct.pack("<HH", *record) for record in SPECS)
    if pointer != table or rom[table-0x08000000:table-0x08000000+len(expected)] != expected:
        raise ValueError("Native save table or its initialization literal differs")
    if len(save) not in (0x20000, 0x20010):
        raise ValueError("Expected 128 KiB battery data with optional 16-byte RTC tail")
    slots = []
    for slot in range(2):
        rows = []
        for sector in range(14):
            offset = (slot * 14 + sector) * 0x1000
            data = save[offset:offset+0x1000]
            section, stored, magic, counter = struct.unpack_from("<HHII", data, 0xFF4)
            actual = checksum(data[:SPECS[section][1]]) if section < 14 else None
            rows.append({"sector": slot * 14 + sector, "section": section,
                         "counter": counter, "magic_valid": magic == 0x08012025,
                         "stored_checksum": stored, "actual_checksum": actual,
                         "checksum_valid": actual is not None and stored == actual})
        ids = [row["section"] for row in rows]
        counters = {row["counter"] for row in rows}
        structure = sorted(ids) == list(range(14)) and len(counters) == 1 and all(
            row["magic_valid"] for row in rows)
        valid = all(row["checksum_valid"] for row in rows)
        slots.append({"slot": slot, "counter": next(iter(counters)) if len(counters) == 1 else None,
                      "complete_structure": structure, "base_checksums_valid": valid,
                      "complete": structure and valid, "sections": rows})
    return {"version": version, "rom_sha256": rom_sha,
            "save_sha256": hashlib.sha256(save).hexdigest(), "save_size": len(save),
            "table_address": f"{table:08x}", "base_checksum_lengths": [size for _, size in SPECS],
            "slots": slots, "valid_slot_count": sum(slot["complete"] for slot in slots),
            "newest_slot_selected": False,
            "scope": "base checksums only; parasite tails, four reserved sectors and RTC tail are not validated",
            "unchecked_section_tails": [{"section": i, "start": size, "end_exclusive": 0xFF0}
                                        for i, (_, size) in enumerate(SPECS) if size < 0xFF0],
            "rtc_tail_size": len(save) - 0x20000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("save", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = analyze_save(args.rom.read_bytes(), args.save.read_bytes())
    write_report(args.report, result, inputs=(args.rom, args.save))
    print(json.dumps({"version": result["version"], "valid_slots": result["valid_slot_count"],
                      "counters": [slot["counter"] for slot in result["slots"]]}))


if __name__ == "__main__":
    main()
