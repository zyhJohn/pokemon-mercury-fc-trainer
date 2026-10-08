import struct
import unittest

from daycare_data import (
    prepare_pending_patch,
    read_daycare_snapshot,
)
from name_codec import encode_name


SHA = "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821"
SAVE1 = 0x02010000
DAYCARE = SAVE1 + 0x2F80
FLAG = SAVE1 + 0xF2C


class Memory:
    def __init__(self, parents=None, *, token=0, flag=0x80):
        self.regions = {
            0x03005008: struct.pack("<I", SAVE1),
            DAYCARE: bytearray(0x11B),
            FLAG: bytes((flag,)),
        }
        if parents:
            for index, raw in enumerate(parents):
                self.regions[DAYCARE][index * 0x8C:(index + 1) * 0x8C] = raw
        struct.pack_into("<H", self.regions[DAYCARE], 0x118, token)

    def read(self, address, count):
        for start, data in self.regions.items():
            if start <= address and address + count <= start + len(data):
                return bytes(data[address - start:address - start + count])
        raise ValueError("missing test memory")


def parent(species=36, pid=0, otid=1234, *, egg=False):
    data = bytearray(0x8C)
    struct.pack_into("<II", data, 0, pid, otid)
    data[8:18] = encode_name("TEST", 10)
    data[20:27] = encode_name("OT", 7)
    data[19] = 0x02 | (4 if egg else 0)
    struct.pack_into("<H", data, 32, species)
    struct.pack_into("<I", data, 72, 0x40000000 if egg else 0)
    struct.pack_into("<I", data, 0x88, 531)
    return bytes(data)


class DaycareDataTests(unittest.TestCase):
    def test_eligible_pair_and_single_byte_patch_preserve_other_bits(self):
        memory = Memory([parent(), parent(132)])
        snapshot = read_daycare_snapshot(memory, SHA)
        self.assertTrue(snapshot.eligible)
        self.assertEqual(snapshot.compatibility_score, 20)
        self.assertEqual(snapshot.parents[0].nickname, "TEST")
        self.assertEqual(snapshot.parents[0].ot_name, "OT")
        self.assertEqual(snapshot.parents[0].gender, "雌性")
        self.assertEqual(snapshot.parents[1].gender, "无性别")
        self.assertEqual(snapshot.parents[0].steps, 531)
        self.assertEqual(len(snapshot.daycare_raw), 0x11B)
        patch = prepare_pending_patch(snapshot)
        self.assertEqual((patch.address, patch.before, patch.after),
                         (FLAG, b"\x80", b"\xC0"))

    def test_rejection_reasons(self):
        cases = (
            (Memory(), "no_parents"),
            (Memory([parent(), bytes(0x8C)]), "one_parent"),
            (Memory([parent(), parent(36)]), "incompatible_parents"),
            (Memory([parent(egg=True), parent(132)]), "parent_egg"),
            (Memory([parent(), parent(132)], token=1), "offspring_token"),
            (Memory([parent(), parent(132)], flag=0x40), "pending_egg"),
            (Memory([parent(1554), parent(132)]), "invalid_parent_record"),
        )
        for memory, reason in cases:
            with self.subTest(reason=reason):
                snapshot = read_daycare_snapshot(memory, SHA)
                self.assertFalse(snapshot.eligible)
                self.assertEqual(snapshot.reason_code, reason)
                with self.assertRaises(ValueError):
                    prepare_pending_patch(snapshot)

    def test_mismatched_egg_mark_and_empty_residue_rejected(self):
        egg_mark = bytearray(parent())
        egg_mark[19] |= 4
        residual = bytearray(parent(species=0))
        for first in (bytes(egg_mark), bytes(residual)):
            snapshot = read_daycare_snapshot(Memory([first, parent(132)]), SHA)
            self.assertEqual(snapshot.reason_code, "invalid_parent_record")

    def test_unknown_rom_and_bad_pointer_refused(self):
        with self.assertRaisesRegex(ValueError, "未知ROM"):
            read_daycare_snapshot(Memory(), "0" * 64)
        memory = Memory([parent(), parent(132)])
        memory.regions[0x03005008] = b"\0\0\0\0"
        with self.assertRaisesRegex(ValueError, "指针无效"):
            read_daycare_snapshot(memory, SHA)


if __name__ == "__main__":
    unittest.main()
