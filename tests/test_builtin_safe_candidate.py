"""Disjoint patch composition, without private ROM or third-party patch data."""

import unittest

from tools.builtin_safe_candidate import apply_plans


class SafeCandidateCompositionTests(unittest.TestCase):
    def test_both_plans_use_original_preimage_and_preserve_other_bytes(self):
        original = b"abcdefgh"
        target, changes = apply_plans(original,
            ((0x08000005, b"fg", b"XY"),), ((0x08000001, b"bc", b"12"),))
        self.assertEqual(target, b"a12deXYh")
        self.assertEqual(original, b"abcdefgh")
        self.assertEqual([row[0] for row in changes], [0x08000001, 0x08000005])

    def test_overlapping_plans_are_rejected_even_with_equal_bytes(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            apply_plans(b"abcd", ((0x08000000, b"ab", b"xy"),),
                        ((0x08000001, b"bc", b"yz"),))

    def test_all_preimages_must_match_unmodified_image(self):
        with self.assertRaisesRegex(ValueError, "preimage"):
            apply_plans(b"abcd", ((0x08000000, b"ab", b"xy"),),
                        ((0x08000002, b"ef", b"zz"),))

    def test_invalid_ranges_and_length_changes_are_rejected(self):
        for patch in ((0x07FFFFFF, b"a", b"x"), (0x08000004, b"a", b"x"),
                      (0x08000000, b"", b""), (0x08000000, b"a", b"xy")):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                apply_plans(b"abcd", (patch,))

    def test_mutable_code_and_noninteger_addresses_are_rejected(self):
        with self.assertRaises(TypeError):
            apply_plans(bytearray(b"abcd"))
        for patch in ((True, b"a", b"b"), (0x08000000, bytearray(b"a"), b"b"),
                      (0x08000000, b"a", bytearray(b"b"))):
            with self.subTest(patch=patch), self.assertRaises(TypeError):
                apply_plans(b"abcd", (patch,))


if __name__ == "__main__":
    unittest.main()
