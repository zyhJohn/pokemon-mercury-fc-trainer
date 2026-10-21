"""Payload-only safety guard for the three pinned builtin-editor candidates.

This is a distinct research derivative of the original UPS. It limits the raw
handler to UI quantities 1..99 and refuses absent-ID/full-pocket additions
before the original AddBagItem's broken -1/u16 comparison can index past RAM.
Native stacking/999 rejection remains unchanged. Never writes an input file.
"""
import hashlib
import struct

from tools.builtin_port_candidate import CANDIDATES

CAVE = 0x08A7FE00
CAVE_SIZE = 0x100
CALL_POINTER = 0x08A7BD60
BASE_SHA256 = frozenset({
    CANDIDATES["1.0"][1], CANDIDATES["1.2"][1],
    "248094b36152be1db91357e0cdfa1a583bfe79676c7129b274c1c605b4c615c0",
})

# Authored Thumb-1 code, assembled at CAVE. This contains no copied ROM code.
# r4=item, r5=quantity; preserve r4-r7/LR and 8-byte stack alignment.
# Native calls: SanitizeItemId -> ItemId_GetPocket -> AddBagItem (if safe).
# The scan accepts a matching ID or an empty slot; the original native function
# retains its exact single-stack behavior, including rejecting 999+1 even if
# another slot is empty. A full unmatched pocket returns 0 without native add.
# Descriptor checks reject capacity 0/>450, misalignment and out-of-EWRAM spans.
GUARD_CODE = bytes.fromhex(
    "f0b581b004460d46002c2fd0012d2dd3632d2bd81a4b00f032f8a04226d12046"
    "184b00f02cf8013804281fd8c000164bc01806688788002f18d0144a974215d8"
    "3146890712d1124a96420fd3394689008919b1420ad30f4a914207d83088a042"
    "06d0002804d00436013ff7d1002004e020462946084b00f002f801b0f0bd1847"
    "a5a80908d9a909088c980302c2010000000000020000040285a00908"
)


def bag_patches(image: bytes) -> tuple[tuple[int, bytes, bytes], ...]:
    """Return two same-length, base-only patches; do not accept hardened images."""
    if not isinstance(image, bytes):
        raise TypeError("Expected immutable ROM bytes")
    if len(image) != 0x02000000 or hashlib.sha256(image).hexdigest() not in BASE_SHA256:
        raise ValueError("Bag guard requires an exact audited base candidate")
    original_pointer = struct.pack("<I", 0x0809A085)
    offset = CALL_POINTER - 0x08000000
    if image[offset:offset+4] != original_pointer:
        raise ValueError("Builtin AddBagItem pointer preimage differs")
    if len(GUARD_CODE) > CAVE_SIZE:
        raise ValueError("Bag safety guard exceeds its owned code cave")
    offset = CAVE - 0x08000000
    blank = b"\xff" * CAVE_SIZE
    if image[offset:offset+CAVE_SIZE] != blank:
        raise ValueError("Complete bag code cave is not unused FF space")
    return ((CALL_POINTER, original_pointer, struct.pack("<I", CAVE | 1)),
            (CAVE, blank, GUARD_CODE.ljust(CAVE_SIZE, b"\xff")))


def harden_bag(image: bytes) -> bytes:
    """Apply only this module's plan to a copy; no file output or registration."""
    output = bytearray(image)
    for address, old, new in bag_patches(image):
        offset = address - 0x08000000
        if len(old) != len(new) or output[offset:offset+len(old)] != old:
            raise ValueError("Bag guard patch plan preimage or length differs")
        output[offset:offset+len(old)] = new
    return bytes(output)
