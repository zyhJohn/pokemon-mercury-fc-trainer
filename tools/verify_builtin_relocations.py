"""Execute relocation research in isolated Unicorn CPUs; never write binaries.

Optional dependencies: unicorn 2.1.4. Synthetic RAM is sufficient for these
ROM-only getters. This does not run a live emulator, BIOS or rendering engine.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ, UcError
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1

from name_codec import decode_name
from tools.builtin_port_candidate import build_candidate, identity
from tools.research_report import write_report
from tools.verify_rom_routines import RomCPU
from ups_patch import apply_ups

GETTERS = {"1.0": 0x09CE9874, "1.2": 0x09CEBC34, "1.33": 0x09CEBC34}
NAME_BASES = {"1.0": 0x09D86FB4, "1.2": 0x09D8B430, "1.33": 0x09D8E858}
TABLE_BASES = {"1.0": 0x097BB0A0, "1.2": 0x097BB0A4, "1.33": 0x097BB0A4}
ROM_BASE = 0x08000000
CONTEXT = 0x02021CD0


def word(image, address):
    return struct.unpack_from("<I", image, address - ROM_BASE)[0]


def engine(image):
    # RomCPU already caps each call at one million instructions. Avoid a
    # per-call timeout worker: on Windows Unicorn's worker join can dominate
    # this many short calls without adding a meaningful extra bound.
    cpu = RomCPU(image, bytes(0x61000), timeout_us=0)
    # Research candidate hashes are deliberately absent from RELEASES. Never
    # rely on implicit ADDRESS_MAPS: every entry in this tool is a native VA.
    cpu.addresses = {}
    return cpu


def result(cpu, entry, ability, species):
    try:
        pointer = cpu.call(entry, ability, species)
        raw = cpu.read(pointer, 32)
        return {"pointer": f"{pointer:08x}", "name": decode_name(raw),
                "terminated_within_32": 255 in raw,
                "prefix_hex": raw[:13].hex()}
    except (UcError, ValueError) as exc:
        return {"error": str(exc), "pc": f"{cpu.cpu.reg_read(UC_ARM_REG_PC):08x}"}


def name_cases(profile):
    """Inputs include all species' ordinary, second and hidden ability IDs."""
    cases = {(129, species) for species in range(1554)}
    contexts = (0, 1, 25, 201, 303, 528, 529, 1241, 1252, 1484, 1553)
    cases.update((ability, species) for ability in range(301) for species in contexts)
    slot_counts = [0, 0, 0]
    for species, record in profile["species"].items():
        for slot, ability in enumerate(record["abilities"]):
            if ability:
                cases.add((ability, int(species)))
                slot_counts[slot] += 1
    return sorted(cases), slot_counts


def verify_names(version, original, patched, profile):
    source, target = engine(original), engine(patched)
    entry, base = GETTERS[version], NAME_BASES[version]
    cases, slot_counts = name_cases(profile)
    failures, special, digest = [], [], hashlib.sha256()
    overrides = {}
    for ability, species in cases:
        before = result(source, entry, ability, species)
        after = result(target, entry, ability, species)
        if before != after or "error" in before:
            failures.append({"ability": ability, "species": species,
                             "original": before, "candidate": after})
        digest.update(json.dumps([ability, species, after], sort_keys=True).encode())
        if ability == 129 and after.get("pointer") != f"{base + 129 * 13:08x}":
            special.append({"species": species, **after})
        if "pointer" in after and after["pointer"] != f"{base + ability * 13:08x}":
            key = (ability, after["pointer"])
            group = overrides.setdefault(key, {"ability": ability, "pointer": after["pointer"],
                                               "name": after["name"], "tested_species": []})
            group["tested_species"].append(species)
    boundaries = []
    for ability, species in [(0, 1), (255, 1), (256, 1), (270, 1), (271, 1),
                             (299, 1), (300, 1), (301, 1), (65535, 1),
                             (0xFFFFFFFF, 1), (129, 65535), (24, 65535)]:
        boundaries.append({"ability": ability, "species": species,
                           "original": result(source, entry, ability, species),
                           "candidate": result(target, entry, ability, species)})
    return {"native_getter": f"{entry:08x}", "normal_hidden_profile_slots": slot_counts,
            "cases": len(cases), "failures": failures,
            "matrix_definition": "profile species ordinary/second/hidden nonzero IDs; IDs 0..300 in 11 representative contexts; ID129 for every species 0..1553",
            "domain_note": "IDs beyond 255 include display-only records and negative probes; successful reads do not establish a legal ability input domain",
            "ability129_all_species_0_to_1553_special_results": special,
            "observed_special_names_within_matrix": list(overrides.values()),
            "results_sha256": digest.hexdigest(), "boundaries": boundaries}


def verify_payload_names(version, image):
    cpu = engine(image)
    entry = GETTERS[version]
    reached, slot_reads = [], []
    def on_entry(uc, address, size, data):
        reached.append({"entry": f"{address:08x}", "ability": uc.reg_read(UC_ARM_REG_R0),
                        "species": uc.reg_read(UC_ARM_REG_R1)})
    handle = cpu.cpu.hook_add(UC_HOOK_CODE, on_entry, begin=entry, end=entry)
    def on_slot_read(uc, access, address, size, value, data):
        slot_reads.append({"address": f"{address:08x}", "size": size,
                           "pc": f"{uc.reg_read(UC_ARM_REG_PC):08x}"})
    read_handle = cpu.cpu.hook_add(UC_HOOK_MEM_READ, on_slot_read,
                                   begin=0x08A7F96C, end=0x08A7F96F)
    cases = []
    for ability, species in [(1, 1), (65, 1), (34, 1), (129, 1), (129, 528),
                             (129, 1241), (129, 1252), (129, 1484), (255, 1553),
                             (0, 0), (300, 1), (301, 1), (65535, 1)]:
        cpu.write(CONTEXT, bytes(0x400))
        cpu.write(CONTEXT + 0x12, struct.pack("<H", species))
        cpu.write(CONTEXT + 0x4E, struct.pack("<H", ability))
        reached.clear()
        slot_reads.clear()
        try:
            pointer = cpu.call(0x08A7D398)
            actual = {"pointer": f"{pointer:08x}",
                      "name": decode_name(cpu.read(pointer, 32)) if pointer else None}
        except (UcError, ValueError) as exc:
            actual = {"error": str(exc), "pc": f"{cpu.cpu.reg_read(UC_ARM_REG_PC):08x}"}
        cases.append({"ability": ability, "species": species,
                      "native_entries": list(reached), "pointer_slot_reads": list(slot_reads),
                      "payload_result": actual})
    cpu.cpu.hook_del(handle)
    cpu.cpu.hook_del(read_handle)
    return {"entry": "08a7d398", "stubs": [], "cases": cases}


def probe_image(image, index, flags=0):
    """Execute real D568 through validator to the first BIOS wrapper boundary.

    Deliberately leave BIOS unmapped: a NULL entry must be visible as a low
    memory read, not silently assigned a synthetic BIOS/open-bus value.
    """
    cpu = engine(image)
    cpu.write(CONTEXT, bytes(0x400))
    cpu.write(CONTEXT + 0x2C, struct.pack("<H", index))
    cpu.write(CONTEXT + 0x38, bytes([flags]))
    reads, faults, boundary = [], [], []
    def on_read(uc, access, address, size, value, data):
        reads.append({"address": f"{address:08x}", "size": size,
                      "pc": f"{uc.reg_read(UC_ARM_REG_PC):08x}"})
    def on_invalid(uc, access, address, size, value, data):
        faults.append({"address": f"{address:08x}", "size": size,
                       "pc": f"{uc.reg_read(UC_ARM_REG_PC):08x}"})
        return False
    def on_boundary(uc, address, size, data):
        boundary.append({"entry": f"{address:08x}",
                         "source": f"{uc.reg_read(UC_ARM_REG_R0):08x}",
                         "destination": f"{uc.reg_read(UC_ARM_REG_R1):08x}"})
        uc.emu_stop()
    cpu.cpu.hook_add(UC_HOOK_MEM_READ, on_read)
    cpu.cpu.hook_add(UC_HOOK_MEM_INVALID, on_invalid)
    cpu.cpu.hook_add(UC_HOOK_CODE, on_boundary, begin=0x08A7A060, end=0x08A7A060)
    error = None
    try:
        cpu.call(0x08A7D568)
    except (UcError, ValueError) as exc:
        if not boundary:
            error = str(exc)
    return {"index": index, "context_flags": flags, "reads": reads,
            "invalid_accesses": faults, "bios_wrapper_boundary": boundary,
            "error": error, "completed_render": False}


def verify_images(version, original, patched):
    base = TABLE_BASES[version]
    empty = [i for i in range(1554) if not word(original, base + 8 * i)]
    cases = [0, 1, 25, empty[0], empty[-1], 1553, 1554, 1555, 2048, 65535]
    return {"physical_table_base": f"{base:08x}",
            "physical_table_entries_not_legal_input_limit": 1554,
            "empty_count": len(empty),
            "cases": [{"table_entry_address": f"{base + index * 8:08x}",
                       "table_pointer": f"{word(original, base + index * 8):08x}",
                       **probe_image(patched, index)} for index in cases],
            "scope": "actual table reads and header validation; stop before BIOS LZ77 VRAM; no graphics stubs"}


def verify_navigation(image):
    """Use the actual payload key callback with a seeded active species menu.

    The input context is controlled RAM, not a claim of opening the whole menu
    through the item engine. No key-handler, validator or render code is stubbed.
    """
    checks = []
    cpu = engine(image)
    for index in (1293, 1294, 1543, 1544, 1553, 1554, 65535):
        checks.append({"index": index, "species_name_validator_A828": cpu.call(0x08A7A828, index)})
    cases = []
    for initial, key in ((1293, 0x80), (1544, 0x40), (1553, 0x80)):
        cpu = engine(image)
        cpu.write(CONTEXT, bytes(0x400))
        cpu.write(CONTEXT, bytes((0x15, 1, 1)))
        cpu.write(CONTEXT + 0x2C, struct.pack("<H", initial))
        cpu.write(word(image, 0x08A7F93C), struct.pack("<I", 0x02001000))
        cpu.write(0x02001000, b"\1")
        cpu.write(word(image, 0x08A7F950) + 0x30, struct.pack("<H", key))
        visited, faults, bios = [], [], []
        milestones = {0x08A7B294, 0x08A7B22C, 0x08A7C940, 0x08A7C648,
                      0x08A7C9EA, 0x08A7CA02, 0x08A7C6F0, 0x08A7B7A8,
                      0x08A7D67C, 0x08A7D568}
        def on_code(uc, address, size, data, milestones=milestones, visited=visited, bios=bios):
            if address in milestones:
                visited.append(f"{address:08x}")
            if address == 0x08A7A060:
                bios.append({"source": f"{uc.reg_read(UC_ARM_REG_R0):08x}",
                             "destination": f"{uc.reg_read(UC_ARM_REG_R1):08x}"})
                uc.emu_stop()
        def on_invalid(uc, access, address, size, value, data, faults=faults):
            faults.append({"address": f"{address:08x}", "size": size,
                           "pc": f"{uc.reg_read(UC_ARM_REG_PC):08x}"})
            return False
        cpu.cpu.hook_add(UC_HOOK_CODE, on_code)
        cpu.cpu.hook_add(UC_HOOK_MEM_INVALID, on_invalid)
        error, returned = None, False
        try:
            cpu.call(0x08A7B1D0, 0)
            returned = True
        except (UcError, ValueError) as exc:
            if not bios:
                error = str(exc)
        cases.append({"initial_index": initial, "key_mask": key,
                      "selected_index": struct.unpack("<H", cpu.read(CONTEXT + 0x2C, 2))[0],
                      "milestones": visited, "invalid_accesses": faults,
                      "bios_wrapper_boundary": bios, "returned": returned, "error": error})
    return {"scope": "actual key callback B1D0 with seeded active mode15/field1/editflag; no full item-open lifecycle",
            "stubs": [], "species_validator_config_F900": word(image, 0x08A7F900),
            "species_name_validator": checks, "cases": cases,
            "direct_only_probes": "1554/1555/2048/65535 direct image calls; ordinary Down from1553 rejects1554; ability301 was a direct getter/D398 injection, not a demonstrated UI-selectable ability"}


def verify(roms, patch, root):
    report = {"scope": "isolated Unicorn ARM execution, synthetic RAM; no live mGBA",
              "binary_files_written": False, "compatibility_registered": False,
              "versions": {}}
    for version, original in roms.items():
        target = (apply_ups(original, patch) if version == "1.33" else
                  build_candidate(original, roms["1.33"], patch).image)
        profile_path = {"1.0": "rom_profile.json", "1.2": "rom_profile_v12.json",
                        "1.33": "rom_profile_v133.json"}[version]
        profile = json.loads((root / profile_path).read_text(encoding="utf-8"))
        if hashlib.sha256(original).hexdigest() != profile["rom_sha256"]:
            raise ValueError("Baseline/profile SHA mismatch")
        report["versions"][version] = {
            "original": identity(original), "candidate": identity(target),
            "native_addresses_explicit_no_release_autorelocation": True,
            "names": verify_names(version, original, target, profile),
            "payload_names": verify_payload_names(version, target),
            "images": verify_images(version, original, target),
            "navigation": verify_navigation(target)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for version in ("10", "12", "133"):
        parser.add_argument("--rom-v" + version, type=Path, required=True)
    parser.add_argument("--ups", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    paths = {"1.0": args.rom_v10, "1.2": args.rom_v12, "1.33": args.rom_v133}
    if args.report.suffix.lower() != ".json" or args.report.resolve() in {
            path.resolve() for path in [*paths.values(), args.ups]}:
        raise ValueError("Report must be a separate JSON path")
    report = verify({v: p.read_bytes() for v, p in paths.items()}, args.ups.read_bytes(),
                    Path(__file__).resolve().parents[1])
    write_report(args.report, report, inputs=(*paths.values(), args.ups))
    print(json.dumps({v: {"name_cases": r["names"]["cases"],
                          "name_failures": len(r["names"]["failures"])}
                      for v, r in report["versions"].items()}))


if __name__ == "__main__":
    main()
