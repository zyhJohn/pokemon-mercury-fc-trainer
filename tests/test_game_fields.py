import unittest

from game_fields import (
    box_name_address,
    decode_box_name,
    encode_box_name,
    prepare_box_name_patch,
    prepare_repel_steps_patch,
    read_box_name,
    read_repel_steps,
)
from rom_versions import RELEASES


class FakeMemory:
    def __init__(self, address, raw):
        self.address = address
        self.raw = raw

    def read(self, address, size):
        if address != self.address or size != len(self.raw):
            raise AssertionError("unexpected read")
        return self.raw


class BoxNameTests(unittest.TestCase):
    def setUp(self):
        self.sha = next(iter(RELEASES))

    def test_index_mapping_covers_25_unique_records(self):
        addresses = [box_name_address(self.sha, i) for i in range(25)]
        self.assertEqual(addresses[0], 0x02031658)
        self.assertEqual(addresses[13], 0x020316CD)
        self.assertEqual(addresses[14], 0x0203164F)
        self.assertEqual(addresses[24], 0x020315F5)
        self.assertEqual(len(set(addresses)), 25)
        with self.assertRaises(ValueError):
            box_name_address(self.sha, 25)
        with self.assertRaises(ValueError):
            box_name_address("0" * 64, 0)

    def test_chinese_mixed_and_full_capacity(self):
        for name in ("大力鳄", "大力鳄A1", "ABCDEFGH", "小智Ab12"):
            raw = encode_box_name(name)
            self.assertEqual(len(raw), 9)
            self.assertEqual(decode_box_name(raw), name)
        self.assertEqual(encode_box_name("ABCDEFGH")[-1], 255)

    def test_rejects_overlong_and_unknown_without_truncating(self):
        for name in ("ABCDEFGHI", "大力鳄A12", "😀", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                encode_box_name(name)
        with self.assertRaises(ValueError):
            decode_box_name(b"\x01\xff" + bytes(7))
        with self.assertRaises(ValueError):
            decode_box_name(b"ABCDEFGHJ")

    def test_patch_is_pure_and_compares_whole_record(self):
        address = box_name_address(self.sha, 0)
        before = encode_box_name("盒子1")
        memory = FakeMemory(address, before)
        self.assertEqual(read_box_name(memory, self.sha, 0), ("盒子1", before))
        patch = prepare_box_name_patch(memory, self.sha, 0, "小智Ab12")
        self.assertEqual((patch.address, patch.before), (address, before))
        self.assertEqual(decode_box_name(patch.after), "小智Ab12")
        self.assertEqual(memory.raw, before)

    def test_repel_patch_only_changes_two_bytes(self):
        memory = FakeMemory(0x0202656C, b"\x02\x00")
        self.assertEqual(read_repel_steps(memory, self.sha), (2, b"\x02\x00"))
        patch = prepare_repel_steps_patch(memory, self.sha, 0)
        self.assertEqual((patch.address, patch.before, patch.after),
                         (0x0202656C, b"\x02\x00", b"\x00\x00"))
        self.assertEqual(memory.raw, b"\x02\x00")

    def test_repel_refuses_unverified_values(self):
        memory = FakeMemory(0x0202656C, b"\x00\x00")
        for steps in (-1, 251, 65535, True):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                prepare_repel_steps_patch(memory, self.sha, steps)
        unknown = FakeMemory(0x0202656C, b"\xfb\x00")
        with self.assertRaises(ValueError):
            prepare_repel_steps_patch(unknown, self.sha, 0)


if __name__ == "__main__":
    unittest.main()
