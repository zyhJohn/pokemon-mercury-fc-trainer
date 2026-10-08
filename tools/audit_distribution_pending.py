"""Read-only, pinned-source audit for three Gen 3 individual PK3 files.

Run: python tools/audit_distribution_pending.py [--rom-v10 PATH --rom-v12 PATH]
The script fetches only PK3 individuals, verifies hashes and prints JSON. It
does not change the catalogue, ROM, emulator state, or save files.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from box_data import BoxPokemon
from name_codec import decode_name
from pokemon_creation import create_box_pokemon
from pokemon_data import experience_for_level

RAW_BASE = "https://raw.githubusercontent.com/projectpokemon/EventsGallery/master/"
SOURCES = (
    ("pokepark-cacnea", "Released/Gen 3/JPN/ポケパーク/Eggs 2005/JoySpot/FRLG - ポケパーク Cacnea (F6925C85) (JPN).pk3", "b1e5ab448c072d8ec7368fb0c69a0d8d97811ae41594139264b6d9d0dfb57a6a", 331),
    ("10-aniv-bulbasaur", "Released/Gen 3/ENG/10th Anniversary Celebration/Journey Across America/Top 20/RSEFL - 10 ANIV Bulbasaur (11B5) (ENG).pk3", "9206d065784efdbe538ffdd44950342f1f1e9eea3baeb26a09b9f4d5236a492d", 1),
    ("10-aniv-articuno", "Released/Gen 3/ENG/10th Anniversary Celebration/Journey Across America/Top 20/RSEFL - 10 ANIV Articuno (4A78) (ENG).pk3", "841e8f501fc8d8d5c8bb741ab4deb9feafb4974174fc896097219c579dae9675", 144),
)


def fetch_pinned(path, expected_sha):
    url = RAW_BASE + quote(path, safe="/")
    raw = urlopen(Request(url, headers={"User-Agent": "MercuryFCTrainer-audit/1"}), timeout=30).read()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha:
        raise ValueError(f"source hash changed: {path}: {actual}")
    return raw, url


def parse(raw, dex):
    if len(raw) not in (80, 100):
        raise ValueError(f"unexpected PK3 length: {len(raw)}")
    checksum = sum(struct.unpack_from("<24H", raw, 32)) & 0xFFFF
    if checksum != struct.unpack_from("<H", raw, 28)[0]:
        raise ValueError("PK3 checksum invalid or data encrypted")
    species, held, experience = struct.unpack_from("<HHI", raw, 32)
    moves = struct.unpack_from("<4H", raw, 44)
    ivword = struct.unpack_from("<I", raw, 72)[0]
    origin = struct.unpack_from("<H", raw, 70)[0]
    ribbons = struct.unpack_from("<I", raw, 76)[0]
    return {
        "source_size": len(raw), "source_checksum": checksum,
        "source_species_internal": species, "source_national_dex": dex,
        "held": held, "experience": experience, "moves": list(moves),
        "pid": struct.unpack_from("<I", raw, 0)[0],
        "otid": struct.unpack_from("<I", raw, 4)[0],
        "language": raw[18], "flags": raw[19], "markings": raw[27],
        "nickname_raw_hex": raw[8:18].hex(), "ot_raw_hex": raw[20:27].hex(),
        "nickname_as_mercury": decode_name(raw[8:18]),
        "ot_as_mercury": decode_name(raw[20:27]),
        "pp_ups": [(raw[40] >> (2 * i)) & 3 for i in range(4)],
        "current_pp": list(raw[52:56]), "friendship": raw[41],
        "evs": list(raw[56:62]),
        "ivs": [(ivword >> (5 * i)) & 31 for i in range(6)],
        "is_egg": bool(ivword & (1 << 30)),
        "source_ability_bit": ivword >> 31,
        "met_location": raw[69], "met_level": origin & 127,
        "source_game_version_code": (origin >> 7) & 15,
        "ball": (origin >> 11) & 15, "ot_gender": origin >> 15,
        "ribbon_word": f"0x{ribbons:08X}",
        "ribbon_bits_0_to_30": ribbons & 0x7FFFFFFF,
        "fateful_encounter": bool(ribbons & 0x80000000),
        "nonzero_contest_or_pokerus": bool(any(raw[62:69])),
        "party_status_nonzero": bool(len(raw) == 100 and any(raw[80:])),
    }


def assess(fields, profiles, index):
    blockers = []
    losses = []
    species = fields["source_species_internal"]
    matched = [r for r in index["categories"]["pokemon"]
               if r["id"] == species and r["dex"] == fields["source_national_dex"]]
    if len(matched) != 1:
        blockers.append("species internal ID and national dex are not uniquely matched")
    move_ids = {r["id"] for r in index["categories"]["moves"]}
    if any(move and move not in move_ids for move in fields["moves"]):
        blockers.append("move ID missing from Mercury catalogue")
    held = fields["held"]
    if held and not any(r["id"] == held for r in index["categories"]["items"]):
        blockers.append("held item ID missing from Mercury catalogue")
    if fields["language"] != 2:
        blockers.append("source language is not Mercury's verified language 2")
    if not fields["nickname_as_mercury"] or not fields["ot_as_mercury"]:
        blockers.append("source name bytes cannot be decoded losslessly by Mercury codec")
    if fields["flags"] != 2 or fields["is_egg"]:
        blockers.append("source bad-egg/egg/presence flags differ from reviewed non-egg case")
    if fields["markings"]:
        blockers.append("source markings cannot be stored in Mercury PC58")
    if fields["ribbon_bits_0_to_30"]:
        blockers.append("source ribbons cannot be stored in Mercury PC58")
    if fields["fateful_encounter"]:
        blockers.append("source fateful-encounter flag cannot be stored in Mercury PC58")
    if fields["nonzero_contest_or_pokerus"]:
        blockers.append("source contest/Pokérus bytes cannot be stored in Mercury PC58")
    if fields["party_status_nonzero"]:
        losses.append("source party status/stats are recreated on withdrawal")
    losses.extend(["source game version code", "source current PP; party stats recomputed on withdrawal"])
    if blockers:
        return {"template_candidate": False, "blockers": blockers, "known_conversion_losses": losses}
    result = []
    for profile in profiles:
        meta = profile["species"][str(species)]
        level = max(level for level in range(1, 101) if
                    experience_for_level(level, meta["growth"], profile["experience_tables"])
                    <= fields["experience"])
        try:
            record = create_box_pokemon(
                profile, species=species, level=level, pid=fields["pid"], otid=fields["otid"],
                nickname=fields["nickname_as_mercury"], ot_name=fields["ot_as_mercury"],
                moves=fields["moves"], pp_ups=fields["pp_ups"], ivs=fields["ivs"],
                evs=fields["evs"], held=held, friendship=fields["friendship"],
                ball=fields["ball"], met_location=fields["met_location"],
                met_level=fields["met_level"], ot_gender=fields["ot_gender"],
                ability_slot=fields["source_ability_bit"] if meta["abilities"][1] else 0,
                experience=fields["experience"],
            )
        except (ValueError, KeyError) as exc:
            blockers.append(f"{profile['name']}: {exc}")
            continue
        if record.ability_flag != fields["source_ability_bit"]:
            blockers.append(f"{profile['name']}: ability bit changed")
        actual = (record.species, record.pid, record.otid, record.nickname,
                  record.ot_name, record.moves, record.pp_ups, record.ivs,
                  record.evs, record.held, record.experience, record.friendship,
                  record.ball, record.met_location, record.met_level, record.ot_gender)
        expected = (species, fields["pid"], fields["otid"],
                    fields["nickname_as_mercury"], fields["ot_as_mercury"],
                    tuple(fields["moves"]), tuple(fields["pp_ups"]),
                    tuple(fields["ivs"]), tuple(fields["evs"]), held,
                    fields["experience"], fields["friendship"], fields["ball"],
                    fields["met_location"], fields["met_level"],
                    fields["ot_gender"])
        if actual != expected:
            blockers.append(f"{profile['name']}: represented PK3 fields changed")
        if record.describe(profile)["errors"]:
            blockers.append(f"{profile['name']}: created record invalid")
        result.append(record.raw)
    if blockers or len(result) != 2 or result[0] != result[1]:
        if len(result) == 2 and result[0] != result[1]:
            blockers.append("V1.0/V1.2 template bytes differ")
        return {"template_candidate": False, "blockers": blockers, "known_conversion_losses": losses}
    record = BoxPokemon(result[0])
    return {"template_candidate": True, "blockers": [], "known_conversion_losses": losses,
            "template_sha256": hashlib.sha256(record.raw).hexdigest(),
            "template_hex": record.raw.hex(), "template_level": level,
            "profile_validation_errors": [record.describe(p)["errors"] for p in profiles]}


def native_fateful_probe(rom, state_path, raw, profile):
    """Toggle only PK3 offset 76 bit31 through the actual ROM compressor."""
    from tools.read_state import read_state
    from tools.verify_creation import COMPRESS, version_for
    from tools.verify_rom_routines import RomCPU

    cpu = RomCPU(rom, read_state(state_path))
    source = bytearray(raw[:80])
    if struct.unpack_from("<I", source, 76)[0] != 0x80000000:
        raise ValueError("probe expects only Cacnea fateful flag in ribbon word")
    outputs = []
    for ribbon_word in (0x80000000, 0):
        struct.pack_into("<I", source, 76, ribbon_word)
        cpu.write(0x2001800, bytes(source))
        cpu.call(COMPRESS[version_for(profile)], 0x2001800, 0x2000800)
        outputs.append(cpu.read(0x2000800, 58))
    return {"same_pc58_with_and_without_fateful_flag": outputs[0] == outputs[1],
            "pc58_sha256": hashlib.sha256(outputs[0]).hexdigest()}


def native_candidate_probe(rom, state_path, fields, assessment, profile):
    """Check candidate compression and full party withdrawal against each ROM."""
    from pokemon_creation import _source80
    from pokemon_data import Pokemon
    from tools.read_state import read_state
    from tools.verify_creation import COMPRESS, verify_record, version_for
    from tools.verify_rom_routines import RomCPU

    species = fields["source_species_internal"]
    metadata = profile["species"][str(species)]
    source80 = _source80(
        profile, species=species, level=assessment["template_level"],
        pid=fields["pid"], otid=fields["otid"],
        nickname=fields["nickname_as_mercury"], ot_name=fields["ot_as_mercury"],
        moves=fields["moves"], pp_ups=fields["pp_ups"], ivs=fields["ivs"],
        evs=fields["evs"], held=fields["held"], friendship=fields["friendship"],
        ball=fields["ball"], met_location=fields["met_location"],
        met_level=fields["met_level"], ot_gender=fields["ot_gender"], egg=False,
        ability_slot=fields["source_ability_bit"] if metadata["abilities"][1] else 0,
        experience=fields["experience"],
    )
    cpu = RomCPU(rom, read_state(state_path))
    cpu.write(0x2001800, source80)
    cpu.call(COMPRESS[version_for(profile)], 0x2001800, 0x2000800)
    native = cpu.read(0x2000800, 58)
    expected = bytes.fromhex(assessment["template_hex"])
    if native != expected:
        raise ValueError("native compressor differs from candidate template")
    party = verify_record(cpu, profile, native)
    withdrawn_pp = list(Pokemon(cpu.read(0x2001400, 100)).pp)
    return {"native_compressor_matches_template": True,
            "native_decompression_and_withdrawal_match": True,
            "species": party["species"], "level": party["level"],
            "pid": party["pid"], "otid": party["otid"],
            "source_current_pp": fields["current_pp"],
            "native_withdrawn_current_pp": withdrawn_pp,
            "party_stats": party["party_stats"]}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--rom-v10", type=Path)
    cli.add_argument("--rom-v12", type=Path)
    cli.add_argument("--state-v10", type=Path)
    cli.add_argument("--state-v12", type=Path)
    args = cli.parse_args()
    profiles = [json.loads((ROOT / p).read_text(encoding="utf-8"))
                for p in ("rom_profile.json", "rom_profile_v12.json")]
    index = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    rom_checks = {}
    rom_bytes = {}
    for label, path, profile in zip(("v10", "v12"), (args.rom_v10, args.rom_v12), profiles):
        if path is not None:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != profile["rom_sha256"]:
                raise ValueError(f"{label} ROM SHA-256 mismatch: {actual}")
            rom_checks[label] = actual
            rom_bytes[label] = path.read_bytes()
    rows = []
    native_probes = {}
    candidate_probes = {}
    for name, path, expected_sha, dex in SOURCES:
        raw, url = fetch_pinned(path, expected_sha)
        fields = parse(raw, dex)
        assessment = assess(fields, profiles, index)
        rows.append({"id": name, "source_url": url, "source_sha256": expected_sha,
                     "fields": fields, "assessment": assessment})
        if name == "pokepark-cacnea":
            for label, state_path, profile in zip(("v10", "v12"),
                                                   (args.state_v10, args.state_v12), profiles):
                if state_path is not None:
                    if label not in rom_bytes:
                        raise ValueError(f"{label} state requires matching ROM")
                    native_probes[label] = native_fateful_probe(
                        rom_bytes[label], state_path, raw, profile)
        elif assessment["template_candidate"]:
            candidate_probes[name] = {}
            for label, state_path, profile in zip(("v10", "v12"),
                                                   (args.state_v10, args.state_v12), profiles):
                if state_path is not None:
                    if label not in rom_bytes:
                        raise ValueError(f"{label} state requires matching ROM")
                    candidate_probes[name][label] = native_candidate_probe(
                        rom_bytes[label], state_path, fields, assessment, profile)
    print(json.dumps({"rom_checks": rom_checks, "native_fateful_probes": native_probes,
                      "native_candidate_probes": candidate_probes,
                      "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
