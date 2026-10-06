"""Read-only isolation checks for Mercury FC's native PC compressor.

This never writes to mGBA or a save file. Reports belong in ignored diagnostics/.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rom_versions import RELEASES, load_profile
from box_data import BoxPokemon
from pokemon_data import Pokemon
from pokemon_creation import _layout, _source80, box_to_party_pokemon, create_box_pokemon
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU


COMPRESS = {"1.0": 0x09D54A94, "1.2": 0x09D58854}
DECOMPRESS = {"1.0": 0x09D54868, "1.2": 0x09D58628}


def version_for(profile):
    return RELEASES[profile["rom_sha256"]][0]


def verify_record(cpu, profile, raw):
    pc = BoxPokemon(raw)
    report = pc.describe(profile)
    if report["errors"]:
        raise ValueError("PC record invalid: " + "; ".join(report["errors"]))
    cpu.write(0x2000800, raw)
    cpu.call(DECOMPRESS[version_for(profile)], 0x2001000, 0x2000800)
    cpu.call(0x0803E774, 0x2001000, 0x2001400)
    mon = Pokemon(cpu.read(0x2001400, 100))
    expected_party = box_to_party_pokemon(profile, pc)
    if mon.raw != expected_party.raw:
        mismatch = [i for i, (a, b) in enumerate(zip(mon.raw, expected_party.raw)) if a != b]
        raise ValueError(f"Native full party record differs at offsets {mismatch}")
    expected = (pc.species, pc.pid, pc.otid, pc.experience, pc.moves,
                pc.pp_ups, pc.ivs, pc.evs, pc.egg, pc.nickname, pc.ot_name,
                pc.met_location, pc.met_level, pc.ball, pc.ot_gender)
    actual = (mon.species, mon.pid, mon.otid, mon.experience, mon.moves,
              mon.pp_ups, mon.ivs, mon.evs, mon.egg, mon.nickname, mon.ot_name,
              mon.met_location, mon.met_level, mon.ball, mon.ot_gender)
    if expected != actual or mon.level != report["level"]:
        raise ValueError("Native decompression/withdrawal changed a created field")
    if mon.validate(profile["species"][str(pc.species)]["base"])["errors"]:
        raise ValueError("Withdrawn party record failed structural validation")
    return {"species": pc.species, "level": report["level"], "pid": pc.pid,
            "otid": pc.otid, "party_stats": mon.stats}


def verify_generated(cpu, profile):
    cases = [
        dict(species=25, level=5, pid=0x12345678, otid=0xABCDEF01,
             nickname="Pika", ot_name="小智", moves=(33, 0, 0, 0)),
        dict(species=1, level=25, pid=0x87654321, otid=0x12345678,
             nickname="妙蛙", ot_name="Ash", moves=(33, 45, 0, 0),
             pp_ups=(3, 1, 0, 0), ivs=(31, 30, 29, 28, 27, 26),
             evs=(100, 80, 60, 40, 20, 0)),
        dict(species=201, level=1, pid=0x11111111, otid=0x98765432,
             nickname="Egg", ot_name="A", moves=(0, 0, 0, 0), egg=True),
        dict(species=308, level=30, pid=0x10203040, otid=0x76543210,
             nickname="Spinda", ot_name="B", moves=(33, 0, 0, 0)),
        dict(species=1141, level=5, pid=0, otid=1,
             nickname="A", ot_name="A", moves=(33, 0, 0, 0)),
        dict(species=1065, level=5, pid=0, otid=1,
             nickname="A", ot_name="A", moves=(33, 0, 0, 0)),
        dict(species=718, level=5, pid=0, otid=1, held=489,
             nickname="A", ot_name="A", moves=(33, 0, 0, 0)),
    ]
    results = []
    for case in cases:
        source = _source80(profile, **{
            "pp_ups": (0, 0, 0, 0), "ivs": (0,) * 6, "evs": (0,) * 6,
            "held": 0, "friendship": None, "ball": 4, "met_location": 0,
            "met_level": None, "ot_gender": 0, "egg": False,
            "ability_slot": None, "experience": None, **case,
        })
        created = create_box_pokemon(profile, **case)
        cpu.write(0x2001800, source)
        cpu.call(COMPRESS[version_for(profile)], 0x2001800, 0x2000800)
        native = cpu.read(0x2000800, 58)
        if native != created.raw:
            mismatch = [i for i, (a, b) in enumerate(zip(native, created.raw)) if a != b]
            raise ValueError(f"Native compressor differs at offsets {mismatch}")
        results.append(verify_record(cpu, profile, created.raw))
    return results


def verify_species_matrix(cpu, profile):
    cases = 0
    rejected_form_combinations = 0
    for ident in map(int, profile["species"]):
        for pid in (0x12345678, 0x12345679):
            case = dict(
                species=ident, level=5, pid=pid, otid=0x87654321,
                nickname="A", ot_name="A", moves=(33, 0, 0, 0),
            )
            try:
                source = _source80(profile, **{
                "pp_ups": (0, 0, 0, 0), "ivs": (0,) * 6, "evs": (0,) * 6,
                "held": 0, "friendship": None, "ball": 4, "met_location": 0,
                "met_level": None, "ot_gender": 0, "egg": False,
                "ability_slot": None, "experience": None, **case,
                })
            except ValueError as exc:
                if "形态" not in str(exc) and "核心颜色" not in str(exc):
                    raise
                rejected_form_combinations += 1
                continue
            created = create_box_pokemon(profile, **case)
            cpu.write(0x2001800, source)
            cpu.call(COMPRESS[version_for(profile)], 0x2001800, 0x2000800)
            if cpu.read(0x2000800, 58) != created.raw:
                raise ValueError(f"Native compressor differs for species {ident} PID {pid}")
            verify_record(cpu, profile, created.raw)
            cases += 1
    return {"verified": cases, "rejected_form_combinations": rejected_form_combinations}


def verify_distribution(cpu, profile):
    path = Path(__file__).resolve().parents[1] / "distributions.json"
    if not path.exists():
        return []
    catalog = json.loads(path.read_text(encoding="utf-8"))
    output = []
    for row in catalog["rows"]:
        template = row.get("template") or {}
        if "native_pc_hex" not in template:
            continue
        raw = bytes.fromhex(template["native_pc_hex"])
        if len(raw) != 58:
            raise ValueError(f"{row['id']}: native template is not 58 bytes")
        output.append({"id": row["id"], **verify_record(cpu, profile, raw)})
    return output


def inspect_forms(rom, state, profile):
    cpu = RomCPU(rom, state)
    species = sorted(map(int, profile["species"]))
    output = {}
    for ident in species:
        pair = []
        for pid in (0, 1):
            source = bytearray(80)
            struct.pack_into("<I", source, 0, pid)
            struct.pack_into("<H", source, 32, ident)
            source[19] = 2  # Native present/valid marker.
            source[18] = 2
            cpu.write(0x2001000, bytes(source))
            cpu.call(COMPRESS[version_for(profile)], 0x2001000, 0x2001400)
            result = cpu.read(0x2001400, 58)
            if struct.unpack_from("<H", result, 28)[0] != ident or result[19] & 7 != 2:
                raise ValueError(f"native compressor damaged species {ident}")
            pair.append(result[19] >> 3)
        output[str(ident)] = pair
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    profile = load_profile(rom)
    state = read_state(args.state)
    cpu = RomCPU(rom, state)
    result = {
        "rom_sha256": profile["rom_sha256"],
        "forms": inspect_forms(rom, state, profile),
        "generated": verify_generated(cpu, profile),
        "species_matrix_cases": verify_species_matrix(cpu, profile),
        "distribution": verify_distribution(cpu, profile),
    }
    if result["forms"] != _layout()["form_codes"]:
        raise ValueError("Committed header-code table differs from native compressor")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rom_sha256": result["rom_sha256"], "species": len(result["forms"]), "generated": len(result["generated"]), "species_matrix_cases": result["species_matrix_cases"], "distribution": len(result["distribution"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
