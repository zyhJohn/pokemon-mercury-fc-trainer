"""Reproduce two-ROM daycare metadata, parent fields and compatibility cases.

Inputs are read only; all synthetic parents and mutations live in private RAM.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daycare_data import read_daycare_snapshot
from rom_versions import RELEASES
from tools.read_state import StateMemory, read_state
from tools.verify_rom_routines import RomCPU


LAYOUT = json.loads(Path(__file__).resolve().parents[1].joinpath(
    "daycare_layout.json").read_text(encoding="utf-8"))
DAYCARE_OFFSET = 0x2F80
PARENT_SIZE = 0x8C


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _read_rom(rom, address, size):
    return rom[address - 0x08000000:address - 0x08000000 + size]


def verify_table(rom, state, sha):
    release = LAYOUT["releases"][sha]
    base = release["base_stats_address"]
    if _u32(rom, 0x1BC) != base:
        raise ValueError("gBaseStats pointer changed")
    engine = RomCPU(rom, state)
    checked = 0
    for ident, values in release["species"].items():
        species = int(ident)
        offset = base + 28 * species
        g1, g2 = _read_rom(rom, offset + 20, 2)
        ratio = _read_rom(rom, offset + 16, 1)[0]
        national = engine.call(release["species_to_national_function"], species)
        if values != [g1, g2, national, ratio]:
            raise ValueError(f"species {species}: metadata does not match ROM")
        # The actual gender routine used by compatibility returns 0 male,
        # 0xFE female and 0xFF genderless.
        for pid in (0, 127, 255):
            expected = (0xFF if ratio == 255 else 0xFE if ratio == 254
                        or ratio and pid < ratio else 0)
            if engine.call(0x0803F78C, species, pid) != expected:
                raise ValueError(f"species {species}: gender ratio differs")
        checked += 1
    if checked != 1432:
        raise ValueError("species table must contain exactly 1432 verified IDs")
    return checked


def _mon(base, species, pid, otid, *, egg=False):
    raw = bytearray(base)
    struct.pack_into("<I", raw, 0, pid)
    struct.pack_into("<I", raw, 4, otid)
    struct.pack_into("<H", raw, 32, species)
    raw[19] = (raw[19] & ~5) | (4 if egg else 0)
    iv = _u32(raw, 72)
    struct.pack_into("<I", raw, 72, (iv & ~0x40000000) | (0x40000000 if egg else 0))
    return bytes(raw)


def _slot(mon):
    return mon + bytes(PARENT_SIZE - 80)


def _private_state(state, address, parents, pending_address):
    copy = bytearray(state)
    offset = 0x21000 + address - 0x02000000
    copy[offset:offset + 2 * PARENT_SIZE] = parents
    copy[offset + 0x118:offset + 0x11B] = b"\0\0\0"
    flag_offset = 0x21000 + pending_address - 0x02000000
    copy[flag_offset] &= ~0x40
    return bytes(copy)


def verify_matrix(rom, state, sha, donor):
    release = LAYOUT["releases"][sha]
    table = release["species"]
    save1 = _u32(StateMemory(state, rom).read(0x03005008, 4), 0)
    daycare = save1 + DAYCARE_OFFSET
    flag = save1 + 0xF2C
    ot = _u32(donor, 4)
    other_ot = ot ^ 0x01234567
    # Pick actual ROM species with verified group/ratio properties, rather
    # than assuming a vanilla species index beyond the two sample parents.
    genderless = next(i for i, v in table.items() if v[3] == 255 and v[0] not in (13, 15))
    undiscovered = next(i for i, v in table.items() if v[0] == 15)
    cases = [
        ("no_parents", None, None, 0, "no_parents"),
        ("one_parent", (36, 0, ot), None, 0, "one_parent"),
        ("same_sex", (36, 0, ot), (36, 0, other_ot), 0, "incompatible_parents"),
        ("genderless_pair", (int(genderless), 0, ot), (36, 255, ot), 0, "incompatible_parents"),
        ("ditto_ditto", (132, 0, ot), (132, 0, other_ot), 0, "incompatible_parents"),
        ("ditto_genderless", (132, 0, ot), (int(genderless), 0, other_ot), 50, None),
        ("undiscovered", (int(undiscovered), 0, ot), (132, 0, other_ot), 0, "incompatible_parents"),
        ("same_species_same_ot", (36, 0, ot), (36, 255, ot), 50, None),
        ("same_species_diff_ot", (36, 0, ot), (36, 255, other_ot), 70, None),
        ("different_species_same_ot", (35, 0, ot), (36, 255, ot), 20, None),
        ("different_species_diff_ot", (35, 0, ot), (36, 255, other_ot), 50, None),
        ("no_overlap", (1, 0, ot), (36, 255, other_ot), 0, "incompatible_parents"),
        ("same_national_forms", (1141, 0, ot), (1193, 255, other_ot), 70, None),
        ("egg_parent", (36, 0, ot, True), (132, 0, ot), 20, "parent_egg"),
    ]
    output = []
    for label, spec_a, spec_b, expected_score, expected_reason in cases:
        def make(spec):
            if spec is None:
                return bytes(PARENT_SIZE)
            species, pid, trainer, *is_egg = spec
            return _slot(_mon(donor, species, pid, trainer, egg=bool(is_egg and is_egg[0])))
        private = _private_state(state, daycare, make(spec_a) + make(spec_b), flag)
        snapshot = read_daycare_snapshot(StateMemory(private, rom), sha)
        engine = RomCPU(rom, private)
        native = engine.call(0x0804654C, daycare)
        if native != expected_score or snapshot.compatibility_score != native:
            raise ValueError(f"{label}: native={native} pure={snapshot.compatibility_score}")
        if snapshot.reason_code != expected_reason:
            raise ValueError(f"{label}: reason={snapshot.reason_code}, expected={expected_reason}")
        for i, parent in enumerate(snapshot.parents):
            address = daycare + i * PARENT_SIZE
            expected_fields = (parent.pid, parent.otid, bool(parent.species),
                               parent.species, int(parent.egg))
            actual_fields = tuple(engine.call(0x0803FD44, address, field)
                                  for field in (0, 1, 5, 11, 45))
            if actual_fields != expected_fields:
                raise ValueError(f"{label}: parent {i} GetBoxMonData differs")
            if parent.species:
                actual_gender = engine.call(0x0803F78C, parent.species, parent.pid)
                wanted_gender = {"雄性": 0, "雌性": 0xFE, "无性别": 0xFF}[parent.gender]
                if actual_gender != wanted_gender:
                    raise ValueError(f"{label}: parent {i} gender differs")
        output.append({"case": label, "score": native,
                       "reason_code": snapshot.reason_code})
    # Bad/unknown records are refused before any ROM call that would index
    # outside the verified gBaseStats and national-number tables.
    bad_cases = []
    for label, mutate in (
        ("unsupported_species", lambda x: struct.pack_into("<H", x, 32, 1554)),
        ("bad_egg_mark", lambda x: x.__setitem__(19, x[19] | 1)),
        ("egg_flags_disagree", lambda x: x.__setitem__(19, x[19] | 4)),
        ("unknown_name_encoding", lambda x: x.__setitem__(slice(8, 10), b"\x06\x02")),
        ("empty_with_residue", lambda x: (struct.pack_into("<H", x, 32, 0), x.__setitem__(0, 1))),
    ):
        candidate = bytearray(donor)
        mutate(candidate)
        private = _private_state(state, daycare, _slot(candidate) + _slot(donor), flag)
        snapshot = read_daycare_snapshot(StateMemory(private, rom), sha)
        if snapshot.reason_code != "invalid_parent_record":
            raise ValueError(f"{label}: invalid record was eligible")
        bad_cases.append(label)
    # The ROM does not require vanilla header bit 1 or a conventional
    # checksum in this plaintext 80-byte format. Do not reject them in parser.
    accepted_legacy_fields = []
    for label, mutate in (
        ("header_has_species_bit_cleared", lambda x: x.__setitem__(19, x[19] & ~2)),
        ("offset28_ffff", lambda x: struct.pack_into("<H", x, 28, 0xFFFF)),
    ):
        candidate = bytearray(donor)
        mutate(candidate)
        private = _private_state(state, daycare, _slot(candidate) + _slot(_mon(donor, 132, 0, ot)), flag)
        snapshot = read_daycare_snapshot(StateMemory(private, rom), sha)
        engine = RomCPU(rom, private)
        if (not snapshot.eligible or snapshot.compatibility_score != 20
                or engine.call(0x0804654C, daycare) != 20
                or engine.call(0x0803FD44, daycare, 5) != 1):
            raise ValueError(f"{label}: valid parent was rejected")
        accepted_legacy_fields.append(label)
    return output, bad_cases, accepted_legacy_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for prefix in ("v10", "v12"):
        parser.add_argument(f"--{prefix}-rom", type=Path, required=True)
        parser.add_argument(f"--{prefix}-state", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    loaded = {}
    for version, prefix in (("1.0", "v10"), ("1.2", "v12")):
        rom = getattr(args, f"{prefix}_rom").read_bytes()
        state = read_state(getattr(args, f"{prefix}_state"))
        sha = hashlib.sha256(rom).hexdigest()
        if sha not in RELEASES or RELEASES[sha][0] != version or sha not in LAYOUT["releases"]:
            raise ValueError(f"{version}: exact ROM identity mismatch")
        loaded[version] = (rom, state, sha)
    rom, state, _ = loaded["1.0"]
    address = _u32(StateMemory(state, rom).read(0x03005008, 4), 0) + DAYCARE_OFFSET
    donor = StateMemory(state, rom).read(address, 80)
    if _u32(donor, 0) == 0 or struct.unpack_from("<H", donor, 32)[0] != 36:
        raise ValueError("V1.0 parent donor changed")
    report = {"passed": True, "releases": {}}
    for version, (rom, state, sha) in loaded.items():
        count = verify_table(rom, state, sha)
        cases, bad, accepted = verify_matrix(rom, state, sha, donor)
        report["releases"][version] = {"sha256": sha, "species_checked": count,
                                       "compatibility_cases": cases,
                                       "invalid_records_refused": bad,
                                       "accepted_plaintext_fields": accepted}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
