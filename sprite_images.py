"""Decode verified ROM icon tiles. No images or ROM binaries are bundled."""

import struct
import zlib
from pokemon_data import unown_form, gender


def icon_species(mon, profile):
    if mon.egg or not mon.species:
        return None  # Keep an explicit text egg marker until its icon is verified.
    if mon.species in profile.get("dynamic_icon_species", []):
        return None
    metadata = profile.get("species", {}).get(str(mon.species), {})
    if metadata and gender(mon.pid, metadata["gender_ratio"]) == "雌性":
        female = profile.get("female_icon_ids", {}).get(str(mon.species))
        if female is not None:
            return female
    return (
        profile["unown_icon_ids"][unown_form(mon.pid)]
        if mon.species == 201
        else mon.species
    )


def icon_png(tiles, palette):
    return tiled_png(tiles, palette, 32, 32)


def tiled_png(tiles, palette, width, height):
    if (
        width % 8
        or height % 8
        or len(tiles) != width * height // 2
        or len(palette) != 32
    ):
        raise ValueError("微缩图数据长度无效")
    colors = []
    for index, (word,) in enumerate(struct.iter_unpack("<H", palette)):
        colors.append(
            bytes(
                (
                    (word & 31) * 255 // 31,
                    ((word >> 5) & 31) * 255 // 31,
                    ((word >> 10) & 31) * 255 // 31,
                    0 if index == 0 else 255,
                )
            )
        )
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            offset = ((y // 8) * (width // 8) + x // 8) * 32 + (y % 8) * 4 + x % 8 // 2
            index = (tiles[offset] >> (4 * (x % 2))) & 15
            pixels.extend(colors[index])

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def read_icons(mem, profile, pokemon):
    output, palettes = {}, {}
    for mon in pokemon:
        ident = icon_species(mon, profile)
        if ident is None or ident in output:
            continue
        metadata = profile.get("icons", {}).get(str(ident))
        if metadata is None:
            continue
        address = metadata["palette"]
        if address not in palettes:
            palettes[address] = mem.read(address, 32)
        output[ident] = icon_png(mem.read(metadata["tiles"], 512), palettes[address])
    return output
