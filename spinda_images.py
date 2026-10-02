"""ROM-backed Spinda front sprite and PID spot rendering."""

import struct
from sprite_images import tiled_png


def lz77(data, expected):
    if (
        len(data) < 4
        or data[0] != 16
        or int.from_bytes(data[1:4], "little") != expected
    ):
        raise ValueError("图像压缩头或长度不符合已验证格式")
    output = bytearray()
    cursor = 4
    try:
        while len(output) < expected:
            flags = data[cursor]
            cursor += 1
            for bit in range(7, -1, -1):
                if flags & (1 << bit):
                    first, second = data[cursor], data[cursor + 1]
                    cursor += 2
                    length = (first >> 4) + 3
                    distance = ((first & 15) << 8 | second) + 1
                    if distance > len(output) or len(output) + length > expected:
                        raise ValueError("图像压缩引用超出范围")
                    for _ in range(length):
                        output.append(output[-distance])
                else:
                    output.append(data[cursor])
                    cursor += 1
                if len(output) == expected:
                    break
    except IndexError:
        raise ValueError("图像压缩数据不完整") from None
    return bytes(output)


def draw_spots(tiles, pid, spots):
    if len(tiles) != 2048 or len(spots) != 144:
        raise ValueError("晃晃斑花纹数据长度无效")
    result = bytearray(tiles)
    for spot in range(4):
        x, y, *masks = struct.unpack_from("<BB16H", spots, spot * 36)
        shift = (pid >> (8 * spot)) & 255
        x = (x + (shift & 15) - 8) & 255
        y = (y + (shift >> 4) - 8) & 255
        for row, mask in enumerate(masks):
            yy = (y + row) & 255
            for col in range(16):
                if not mask & (1 << col):
                    continue
                xx = x + col
                if not 0 <= xx < 64 or not 0 <= yy < 64:
                    continue  # Clip the front sprite; never follow engine overflow outside its buffer.
                offset = ((yy // 8) * 8 + xx // 8) * 32 + (yy % 8) * 4 + xx % 8 // 2
                nibble = 4 * (xx & 1)
                color = (result[offset] >> nibble) & 15
                if 1 <= color <= 3:
                    result[offset] += 4 << nibble
    return bytes(result)


def read_spinda_assets(mem, profile):
    layout = profile["spinda"]
    return {
        "tiles": lz77(mem.read(layout["front"], 4096), 2048),
        "palette": lz77(mem.read(layout["palette"], 256), 32),
        "shiny_palette": lz77(mem.read(layout["shiny_palette"], 256), 32),
        "spots": mem.read(layout["spots"], 144),
    }


def spinda_png(assets, pid, shiny):
    return tiled_png(
        draw_spots(assets["tiles"], pid, assets["spots"]),
        assets["shiny_palette" if shiny else "palette"],
        64,
        64,
    )
