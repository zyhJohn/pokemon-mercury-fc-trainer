"""Explicit image/palette safety derivative of three exact research bases.

Pure in-memory patch planning only. This is not a supported-ROM registration.
The original UPS is unchanged. FD00..FD80 is owned entirely by this module.
"""

import hashlib
import struct

BASE = 0x08000000
CAVE = 0x08A7FD00
CAVE_SIZE = 0x80
SITES = ((0x08A7D572, CAVE), (0x08A7D59C, CAVE + 6))
SITE_PREIMAGE = bytes.fromhex("9c8de400")
BASE_SHA256 = frozenset((
    "362beb27cd6b4f4d52470fa20f0d6fe983e8ed884f25aa13d8c84bd2a0fa3179",
    "b26e4ba349a0d5e9a52ce4e38fdec7ef1adcbe1ffef8584e592f52fb61eeb87d",
    "248094b36152be1db91357e0cdfa1a583bfe79676c7129b274c1c605b4c615c0",
))


def thumb_bl(site, target):
    """Encode the ARM7TDMI Thumb-1 BL pair, requiring an aligned target."""
    delta = target - site - 4
    if site & 1 or target & 1 or not -(1 << 22) <= delta < (1 << 22):
        raise ValueError("Invalid Thumb BL source/target")
    return struct.pack("<HH", 0xF000 | ((delta >> 12) & 0x7FF),
                       0xF800 | ((delta >> 1) & 0x7FF))


def helper_bytes():
    """Two table-base entries; preserve all registers except original r4 output.

    A: push r0/r1, r0=r5, branch common; B: push r0/r1, r0=r6.
    Common: bound check before table read, then NULL check, output index*8/0.
    BL's return LR is untouched, and SP is restored before BX LR. Conditions
    differ from the displaced LSLS, but the following ADDS overwrites them.
    """
    code = struct.pack("<18H", *(
        0xB403, 0x4628, 0xE001,  # FD00 push; mov r0,r5; b FD0A
        0xB403, 0x4630,          # FD06 push; mov r0,r6
        0x8D9C, 0x4905, 0x428C, # FD0A ldrh r4,[r3,2C]; ldr r1,[FD24]; cmp
        0xD805, 0x00E4, 0x5901, # FD10 bhi FD1E; lsl r4,3; ldr r1,[r0,r4]
        0x2900, 0xD001,          # FD16 cmp r1,0; beq FD1E
        0xBC03, 0x4770,          # FD1A pop r0/r1; bx lr
        0x2400, 0xE7FB, 0x46C0, # FD1E mov r4,0; b FD1A; alignment nop
    )) + struct.pack("<I", 1553)
    return code + b"\xff" * (CAVE_SIZE - len(code))


def _read_word(image, address):
    offset = address - BASE
    if offset < 0 or offset + 4 > len(image):
        raise ValueError("Fallback pointer outside verified ROM")
    return struct.unpack_from("<I", image, offset)[0]


def _verify_fallbacks(image):
    # The original renderer independently validates the resource header. Ensure
    # fallback item zero in every used table is an actual correct-size resource.
    for pointer_slot, size in ((0x08A7FA98, 0x800), (0x08000130, 0x20),
                               (0x08000134, 0x20)):
        table = _read_word(image, pointer_slot)
        pointer = _read_word(image, table)
        if _read_word(image, pointer) != (size << 8 | 0x10):
            raise ValueError("Image/palette fallback item zero is invalid")


def image_patches(image: bytes) -> tuple[tuple[int, bytes, bytes], ...]:
    """Return an exact-base-only, equal-length, nonoverlapping patch plan."""
    if not isinstance(image, bytes):
        raise TypeError("Image guard input must be immutable bytes")
    if len(image) != 0x02000000 or hashlib.sha256(image).hexdigest() not in BASE_SHA256:
        raise ValueError("Image guard requires an exact audited research base")
    for site, _ in SITES:
        if image[site - BASE:site - BASE + 4] != SITE_PREIMAGE:
            raise ValueError("Image guard site preimage mismatch")
    expected = b"\xff" * CAVE_SIZE
    if image[CAVE - BASE:CAVE - BASE + CAVE_SIZE] != expected:
        raise ValueError("Image guard full cave preimage mismatch")
    _verify_fallbacks(image)
    return tuple((site, SITE_PREIMAGE, thumb_bl(site, target)) for site, target in SITES) + (
        (CAVE, expected, helper_bytes()),)


def harden_images(image: bytes) -> bytes:
    """Apply only this module's verified plan to a new in-memory copy."""
    patches = image_patches(image)
    result = bytearray(image)
    for address, expected, replacement in patches:
        offset = address - BASE
        if len(expected) != len(replacement) or result[offset:offset + len(expected)] != expected:
            raise ValueError("Image guard plan preimage/length mismatch")
        result[offset:offset + len(expected)] = replacement
    return bytes(result)
