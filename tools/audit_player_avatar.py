"""Read-only research of gender caches and custom avatar overrides."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_rom_routines import RomCPU
from tools.read_state import read_state


def audit(rom, state, profile):
    if hashlib.sha256(rom).hexdigest() != profile["rom_sha256"]:
        raise ValueError("ROM SHA-256 不匹配")
    engine = RomCPU(rom, state)
    avatar = struct.unpack_from("<I", rom, 0x5C96C)[0]
    if avatar != 0x02037078:
        raise ValueError("Avatar location differs from verified ROM")
    save2 = struct.unpack("<I", engine.read(0x300500C, 4))[0]
    if not 0x2000000 <= save2 <= 0x203FFF2:
        raise ValueError("SaveBlock2 pointer invalid")
    original_gender = engine.read(save2 + 8, 1)
    original_avatar = engine.read(avatar + 7, 1)
    explicit = [[engine.call(0x805C7C8, s, g) for s in range(8)] for g in range(2)]
    cached = []
    save_only = []
    for g in range(2):
        engine.write(save2 + 8, bytes([g]))
        save_only.append([engine.call(0x805C808, s) for s in range(8)])
    engine.write(save2 + 8, original_gender)
    for g in range(2):
        engine.write(avatar + 7, bytes([g]))
        cached.append([engine.call(0x805C808, s) for s in range(8)])
    engine.write(avatar + 7, original_avatar)
    variables = struct.unpack_from("<7I", rom, 0x1D0D24C)
    overrides = {f"{var:04X}": engine.call(0x806E568, var) for var in variables}
    return {
        "rom_sha256": profile["rom_sha256"],
        "avatar_address": avatar,
        "saved_gender": original_gender[0],
        "cached_gender": original_avatar[0],
        "explicit_gender_graphics": explicit,
        "save_only_graphics": save_only,
        "cache_only_graphics": cached,
        "avatar_override_vars": overrides,
        "gender_edit_enabled": False,
        "scope": "Isolated CPU only. Avatar/sprite reinitialization, map transitions, script choices and live save/reload unverified.",
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
    result = audit(args.rom.read_bytes(), read_state(args.state), profile)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
