"""Private offline verification report; no ROM/save data is changed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse, json, hashlib
from tools.read_state import StateMemory, read_state
from trainer_core import Trainer
from pokemon_data import calculate_stats, experience_for_level


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("rom_profile.json")
        .read_text(encoding="utf-8")
    )
    rom = args.rom.read_bytes()
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 与核验对象不一致")
    trainer = Trainer(StateMemory(read_state(args.state), rom), profile, "backups")
    snap = trainer.snapshot()
    result = {
        "rom_sha256": profile["rom_sha256"],
        "state_sha256": hashlib.sha256(args.state.read_bytes()).hexdigest(),
        "party": [],
        "pockets": [],
    }
    for i, mon in enumerate(snap["party"]):
        base = trainer.base_stats(mon.species)
        growth = profile["species"][str(mon.species)]["growth"]
        expected = calculate_stats(
            base, mon.ivs, mon.evs, mon.level, mon.pid % 25, mon.species == 303
        )
        exp_ok = experience_for_level(
            mon.level, growth, profile["experience_tables"]
        ) <= mon.experience and (
            mon.level == 100
            or mon.experience
            < experience_for_level(mon.level + 1, growth, profile["experience_tables"])
        )
        result["party"].append(
            {
                "slot": i + 1,
                "species": mon.species,
                "level": mon.level,
                "shiny": mon.shiny,
                "stats_match": expected == mon.stats,
                "experience_matches_level": exp_ok,
                "validation": trainer.validate_pokemon(mon),
            }
        )
    import struct

    for pocket_id in range(1, 6):
        s = trainer.snapshot(pocket_id)
        items = [
            {"slot": i + 1, "id": a, "quantity": b}
            for i, (a, b) in enumerate(struct.iter_unpack("<HH", s["bag"]))
            if a
        ]
        result["pockets"].append(
            {
                "definition": s["pocket"],
                "first_items": items[:4],
                "occupied": len(items),
            }
        )
    result["passed"] = all(
        row["stats_match"]
        and row["experience_matches_level"]
        and row["validation"]["structural_ok"]
        for row in result["party"]
    )
    result["scope"] = (
        "Offline structure, experience and stats checks; not full encounter legality or live write verification."
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
