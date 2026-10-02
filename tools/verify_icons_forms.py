"""Compare icon pointers and form choices against actual ROM functions."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from tools.verify_rom_routines import RomCPU
from tools.read_state import read_state
from pokemon_data import change_unown_letter_pid, unown_form
from sprite_images import icon_species, icon_png


def verify(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    icons = forms = 0
    for ident, item in profile["icons"].items():
        species = int(ident)
        if engine.call(0x8097028, species, 0) != item["tiles"]:
            raise ValueError("Icon tile pointer mismatch")
        if engine.call(0x80971CC, species) != item["palette"]:
            raise ValueError("Icon palette pointer mismatch")
        icon_png(engine.read(item["tiles"], 512), engine.read(item["palette"], 32))
        for pid in [0, 255]:
            mon = SimpleNamespace(species=species, pid=pid, egg=False)
            expected = icon_species(mon, profile)
            if (
                expected is not None
                and engine.call(0x8096F5C, species, pid, 0) != expected
            ):
                raise ValueError(f"Icon identity mismatch for {species} / {pid}")
        icons += 1
    for pid in [0x12345678, 0x12341234]:
        for letter in range(28):
            result = change_unown_letter_pid(pid, 0, letter)
            if engine.call(0x8082AB8, result) != letter or unown_form(result) != letter:
                raise ValueError("Letter differs from ROM")
            if (
                engine.call(0x8096F5C, 201, result, 0)
                != profile["unown_icon_ids"][letter]
            ):
                raise ValueError("Unown icon differs from ROM")
            forms += 1
    return {
        "passed": True,
        "icons": icons,
        "form_cases": forms,
        "scope": "Isolated ROM display functions; not live mGBA persistence.",
    }


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
    result = verify(args.rom.read_bytes(), read_state(args.state), profile)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
