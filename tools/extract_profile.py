"""Reproduce numeric metadata from the exact locally verified Mercury FC ROM.

No game binary is distributed. An unknown ROM is rejected instead of guessing
that a superficially similar code/header has the same data layout.
"""

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

ROM_SHA256 = "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821"
SIGNATURES = [
    (0x80000AC, 4),
    (0x803F94C, 8),
    (0x803F906, 2),
    (0x803F90C, 2),
    (0x803F92A, 2),
    (0x803F930, 2),
    (0x803FD9C, 14),
    (0x8040524, 14),
    (0x8040AE6, 2),
    (0x804449C, 34),
    (0x80001BC, 4),
    (0x9DD6250, 40),
    (0x80001CC, 4),
    (0x8040D38, 8),
    (0x9D07C3C, 104),
    (0x9D628A8, 72),
    (0x803E7C4, 108),
    (0x803E47C, 8),
    (0x9D070C4, 24),
    (0x8043E20, 4),
    (0x9D41AF8, 56),
    (0x8043C2C, 8),
    (0x8043C68, 4),
    (0x9D3D9C4, 144),
    (0x8125A78, 8),
    (0x8125A8C, 4),
    (0x9D3DCA4, 20),
    (0x9D3D852, 36),
    (0x9D3D69E, 10),
    (0x9D073D6, 10),
    (0x9D07430, 8),
    (0x80CC1E4, 32),
    (0x803FBE8, 26),
    (0x804037C, 26),
    (0x8043458, 284),
    (0x8005842, 12),
    (0x8006920, 4),
    (0x820F658, 138),
    (0x9D30C04, 88),
    (0x9D30C5C, 640),
    (0x9DD5E68, 16),
]


def extract(rom, catalog):
    if hashlib.sha256(rom).hexdigest() != ROM_SHA256:
        raise ValueError("不是已验证的 ROM SHA-256")

    def read(a, n):
        return rom[a - 0x08000000 : a - 0x08000000 + n]

    base = struct.unpack_from("<I", rom, 0x1BC)[0]
    moves = struct.unpack_from("<I", rom, 0x1CC)[0]
    profile = {
        "name": "Mercury FC 1.0 / verified 2026-10-02",
        "rom_sha256": ROM_SHA256,
        "rom_crc32": f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}",
        "layout": "fixed-gaem-plaintext",
        "shiny_threshold": 8,
        "checksum": "disabled-preserve-header",
        "base_stats_address": base,
        "item_table_address": 0x87C7E00,
        "signatures": [],
        "species": {},
        "items": {},
        "pockets": [],
        "moves_address": moves,
        "moves": {},
    }
    for address, n in SIGNATURES:
        profile["signatures"].append(
            {"address": address, "hex": read(address, n).hex()}
        )
    experience = struct.unpack("<I", read(0x803E828, 4))[0]
    profile["experience_address"] = experience
    profile["experience_tables"] = {
        str(g): list(struct.unpack("<101I", read(experience + g * 1024, 404)))
        for g in range(6)
    }
    learnsets = struct.unpack("<I", read(0x8043E20, 4))[0]
    profile["level_up_address"] = learnsets
    profile["level_up_learnsets"] = {}
    tm_moves = struct.unpack("<I", read(0x8125A8C, 4))[0]
    tm_compatibility = struct.unpack("<I", read(0x8043C68, 4))[0]
    profile["tm_moves_address"] = tm_moves
    profile["tm_compatibility_address"] = tm_compatibility
    profile["tm_count"] = 120
    profile["tm_moves"] = list(struct.unpack("<128H", read(tm_moves, 256)))
    profile["tm_compatibility"] = {}
    profile["battle_flag"] = {"address": 0x03003529, "mask": 2}
    profile["trainer"] = {
        "pointer_address": 0x0300500C,
        "id_offset": 10,
        "header_size": 14,
    }
    profile["name_encoding"] = {
        "chinese_characters": 6763,
        "maximum_bytes": 7,
        "renderer": 0x820F658,
        "small_font": 0x8840000,
        "normal_font": 0x87D0000,
    }
    profile["minior"] = {
        "shell_species": 991,
        "core_species": list(struct.unpack("<7H", read(0x9DD5E68, 14))),
        "colors": ["红色", "蓝色", "橙色", "黄色", "靛色", "绿色", "紫色"],
        "pid_selector": 0x9D30C04,
        "form_reversion": 0x9D30C5C,
        "backup_species_offset": 28,
    }
    profile["spinda"] = {
        "front": struct.unpack("<I", read(0x97BBA40, 4))[0],
        "palette": struct.unpack("<I", read(0x97D6E60, 4))[0],
        "shiny_palette": struct.unpack("<I", read(0x97E5368, 4))[0],
        "spots": struct.unpack("<I", read(0x8043520, 4))[0],
    }
    icon_table = struct.unpack("<I", read(0x8097050, 4))[0]
    palette_records = struct.unpack("<I", read(0x80971F0, 4))[0]
    palette_indices = struct.unpack("<I", read(0x80971F4, 4))[0]
    profile["icons"] = {}
    for row in catalog["categories"]["pokemon"]:
        ident = row["id"]
        tile = struct.unpack("<I", read(icon_table + ident * 4, 4))[0]
        index = read(palette_indices + ident, 1)[0]
        palette = struct.unpack("<I", read(palette_records + index * 8, 4))[0]
        if not (
            0x8000000 <= tile <= 0xA000000 - 512
            and 0x8000000 <= palette <= 0xA000000 - 32
        ):
            raise ValueError("Icon pointers outside verified ROM")
        profile["icons"][str(ident)] = {"tiles": tile, "palette": palette}
    profile["unown_icon_ids"] = [201] + list(range(413, 440))
    profile["unown_letters"] = [chr(65 + i) for i in range(26)] + ["!", "?"]
    profile["female_icon_ids"] = {
        "502": 744,
        "503": 745,
        "574": 703,
        "645": 704,
        "646": 705,
        "776": 831,
    }
    profile["dynamic_icon_species"] = [824]
    storage_table = 0x9DD71AC
    profile["storage"] = {
        "record_size": 58,
        "slots_per_box": 30,
        "box_addresses": list(struct.unpack("<25I", read(storage_table, 100))),
        "signatures": [
            {"address": a, "hex": read(a, n).hex()}
            for a, n in [
                (0x808BA18, 8),
                (0x9D54A44, 80),
                (0x9D54868, 212),
                (storage_table, 100),
            ]
        ],
    }
    for row in catalog["categories"]["pokemon"]:
        ident = row["id"]
        b = read(base + ident * 28, 28)
        if len(b) == 28 and min(b[:6]) > 0:
            profile["species"][str(ident)] = {
                "base": list(b[:6]),
                "gender_ratio": b[16],
                "egg_cycles": b[17],
                "friendship": b[18],
                "growth": b[19],
                "abilities": [b[22], b[23], b[26]],
                "name": row["name"],
            }
            pointer = struct.unpack("<I", read(learnsets + ident * 4, 4))[0]
            entries = []
            for index in range(256):
                move, level = struct.unpack("<HB", read(pointer + 3 * index, 3))
                if move == 0 and level == 255:
                    break
                if not 1 <= move <= 1023 or not 0 <= level <= 100:
                    raise ValueError(f"物种 {ident} 的等级招式表格式异常")
                entries.append([move, level])
            else:
                raise ValueError(f"物种 {ident} 的等级招式表没有终止标志")
            profile["level_up_learnsets"][str(ident)] = entries
            bits = int.from_bytes(read(tm_compatibility + ident * 16, 16), "little")
            profile["tm_compatibility"][str(ident)] = [
                i for i in range(128) if bits & (1 << i)
            ]
    for ident in range(750):
        b = read(0x87C7E00 + ident * 44, 44)
        if struct.unpack_from("<H", b, 14)[0] == ident and 1 <= b[26] <= 5:
            profile["items"][str(ident)] = {
                "pocket": b[26],
                "price": struct.unpack_from("<H", b, 16)[0],
            }
            if b[26] == 4:
                index = b[25] - 1 if b[25] else (ident - 289) & 255
                if not 0 <= index < 128:
                    raise ValueError(f"学习器 {ident} 的索引异常")
                profile["items"][str(ident)].update(
                    tm_index=index, move=profile["tm_moves"][index]
                )
    for i, name in enumerate(["道具", "重要道具", "精灵球", "招式学习器盒", "树果袋"]):
        address, capacity = struct.unpack("<II", read(0x9DD6250 + i * 8, 8))
        profile["pockets"].append(
            {
                "id": i + 1,
                "name": name,
                "address": address,
                "capacity": capacity,
                "quantity_visible": i != 1,
            }
        )
    for row in catalog["categories"]["moves"]:
        ident = row["id"]
        b = read(moves + ident * 12, 12)
        if len(b) == 12 and 1 <= b[4] <= 64:
            profile["moves"][str(ident)] = {
                "pp": b[4],
                "power": b[1],
                "accuracy": b[3],
                "type": b[2],
                "category": b[10],
                "name": row["name"],
            }
    return profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract(
        args.rom.read_bytes(), json.loads(args.catalog.read_text(encoding="utf-8"))
    )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Profile regenerated from verified ROM")


if __name__ == "__main__":
    main()
