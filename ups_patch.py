"""Bounded, in-memory UPS1 parsing and explicit-direction application.

UPS uses byuu's biased base-128 integers, zero-extended XOR data and a
zero byte which advances the image cursor even when it is at image EOF.
Format reference: https://github.com/Alcaro/Flips/blob/master/libups.cpp
No file access, input mutation or automatic direction selection is performed.
"""

from dataclasses import dataclass
import zlib


MAX_IMAGE_SIZE = 64 * 1024 * 1024


class UpsPatchError(ValueError):
    """Malformed UPS data, unsupported size or an identity/checksum mismatch."""


@dataclass(frozen=True)
class UpsPatch:
    source_size: int
    target_size: int
    source_crc32: str
    target_crc32: str
    patch_crc32: str
    records: tuple[tuple[int, bytes], ...]


def _crc32(data: bytes) -> str:
    return f"{zlib.crc32(data):08x}"


def parse_ups(patch: bytes) -> UpsPatch:
    """Validate a UPS1 patch and return absolute-offset XOR records.

    Records exclude their zero terminators. The final terminator may occur at
    ``max(source_size, target_size)``; it does not represent an image byte.
    All nonzero XOR bytes must fit the larger image. Declared images are
    limited to MAX_IMAGE_SIZE before application allocates any image buffer.
    """
    if not isinstance(patch, bytes):
        raise TypeError("UPS patch must be bytes")
    if len(patch) < 18 or patch[:4] != b"UPS1":
        raise UpsPatchError("Invalid or truncated UPS1 header")
    footer = len(patch) - 12
    source_crc = f"{int.from_bytes(patch[footer:footer + 4], 'little'):08x}"
    target_crc = f"{int.from_bytes(patch[footer + 4:footer + 8], 'little'):08x}"
    patch_crc = f"{int.from_bytes(patch[-4:], 'little'):08x}"
    if _crc32(memoryview(patch)[:-4]) != patch_crc:
        raise UpsPatchError("UPS patch CRC32 mismatch")

    position = 4

    def read_number(limit: int) -> int:
        nonlocal position
        value, weight = 0, 1
        while position < footer:
            byte = patch[position]
            position += 1
            value += (byte & 0x7f) * weight
            if value > limit:
                raise UpsPatchError("UPS size or offset exceeds supported bounds")
            if byte & 0x80:
                return value
            weight <<= 7
            value += weight
            if value > limit:
                raise UpsPatchError("UPS size or offset exceeds supported bounds")
        raise UpsPatchError("Truncated UPS variable-length number")

    source_size = read_number(MAX_IMAGE_SIZE)
    target_size = read_number(MAX_IMAGE_SIZE)
    image_size = max(source_size, target_size)
    cursor = 0
    records = []
    while position < footer:
        cursor += read_number(image_size)
        if cursor > image_size:
            raise UpsPatchError("UPS record offset is outside the image")
        terminator = patch.find(b"\0", position, footer)
        if terminator < 0:
            raise UpsPatchError("Unterminated UPS XOR record")
        length = terminator - position
        if length > image_size - cursor:
            raise UpsPatchError("UPS XOR record extends past the image")
        records.append((cursor, patch[position:terminator]))
        cursor += length + 1
        position = terminator + 1

    return UpsPatch(source_size, target_size, source_crc, target_crc,
                    patch_crc, tuple(records))


def apply_ups(source: bytes, patch: bytes, *, reverse: bool = False) -> bytes:
    """Apply validated UPS bytes; reverse=True explicitly swaps identities."""
    if not isinstance(source, bytes):
        raise TypeError("UPS source must be bytes")
    if not isinstance(reverse, bool):
        raise TypeError("UPS reverse flag must be bool")
    parsed = parse_ups(patch)
    if reverse:
        input_size, output_size = parsed.target_size, parsed.source_size
        input_crc, output_crc = parsed.target_crc32, parsed.source_crc32
    else:
        input_size, output_size = parsed.source_size, parsed.target_size
        input_crc, output_crc = parsed.source_crc32, parsed.target_crc32
    if len(source) != input_size:
        raise UpsPatchError("UPS source size mismatch")
    if _crc32(source) != input_crc:
        raise UpsPatchError("UPS source CRC32 mismatch")
    output = bytearray(source[:output_size])
    if len(output) < output_size:
        output.extend(b"\0" * (output_size - len(output)))
    for offset, xor_bytes in parsed.records:
        for index in range(min(len(xor_bytes), max(0, output_size - offset))):
            output[offset + index] ^= xor_bytes[index]
    if _crc32(output) != output_crc:
        raise UpsPatchError("UPS target CRC32 mismatch")
    return bytes(output)
