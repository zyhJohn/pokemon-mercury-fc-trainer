"""Synthetic save structures; no private ROM or save bytes."""

import hashlib
import struct
import unittest
from unittest.mock import patch

from tools.inspect_mercury_save import SPECS, TABLES, analyze_save, checksum


class SaveInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rom = bytearray(0x2000000)
        table = TABLES["1.2"]
        struct.pack_into("<I", rom, 0xDA23C, table)
        rom[table-0x08000000:table-0x08000000+56] = b"".join(struct.pack("<HH", *r) for r in SPECS)
        cls.rom = bytes(rom)
        cls.sha = hashlib.sha256(cls.rom).hexdigest()

    def save(self, counters=(0xFFFFFFFF, 0), rtc=False):
        save = bytearray(b"\xff" * (0x20010 if rtc else 0x20000))
        for slot, counter in enumerate(counters):
            for sector in range(14):
                section = (sector + slot + 5) % 14
                offset = (slot * 14 + sector) * 0x1000
                data = bytes([section + 1]) * SPECS[section][1]
                save[offset:offset+len(data)] = data
                struct.pack_into("<HHII", save, offset+0xFF4, section, checksum(data), 0x08012025, counter)
        return bytes(save)

    def analyze(self, save, rom=None):
        image = self.rom if rom is None else rom
        with patch("tools.inspect_mercury_save.AUDITED_ROMS", {hashlib.sha256(image).hexdigest(): "1.2"}):
            return analyze_save(image, save)

    def test_fold_and_u32_wrap_known_vectors(self):
        self.assertEqual(checksum(struct.pack("<I", 0xFFFF0001)), 0)
        self.assertEqual(checksum(struct.pack("<II", 0xFFFFFFFF, 1)), 0)
        self.assertEqual(checksum(struct.pack("<II", 0x00010002, 0x00030004)), 10)
        for value in (b"", b"123", bytearray(4)):
            with self.assertRaises(ValueError):
                checksum(value)

    def test_rotated_complete_slots_report_wrap_without_guessing_latest(self):
        result = self.analyze(self.save(rtc=True))
        self.assertEqual(result["valid_slot_count"], 2)
        self.assertEqual([slot["counter"] for slot in result["slots"]], [0xFFFFFFFF, 0])
        self.assertFalse(result["newest_slot_selected"])
        self.assertEqual(result["rtc_tail_size"], 16)
        self.assertEqual(len(result["unchecked_section_tails"]), 3)

    def test_base_corruption_is_rejected_but_other_slot_survives(self):
        save = bytearray(self.save())
        save[0] ^= 1
        result = self.analyze(bytes(save))
        self.assertFalse(result["slots"][0]["base_checksums_valid"])
        self.assertEqual(result["valid_slot_count"], 1)

    def test_duplicate_ids_mixed_counters_and_bad_magic_are_incomplete(self):
        for field, fmt, value in ((0xFF4, "H", 6), (0xFFC, "I", 123), (0xFF8, "I", 0)):
            save = bytearray(self.save())
            struct.pack_into("<"+fmt, save, field, value)
            with self.subTest(field=field):
                self.assertFalse(self.analyze(bytes(save))["slots"][0]["complete_structure"])

    def test_parasite_tail_change_is_explicitly_outside_base_checksum(self):
        save = bytearray(self.save())
        # Locate section0 within the rotated first slot and change its tail.
        sector = next(i for i in range(14) if struct.unpack_from("<H", save, i*0x1000+0xFF4)[0] == 0)
        save[sector*0x1000+0xF24] ^= 1
        result = self.analyze(bytes(save))
        self.assertTrue(result["slots"][0]["complete"])
        self.assertIn("parasite tails", result["scope"])

    def test_unknown_rom_bad_literal_and_bad_sizes_are_rejected(self):
        with self.assertRaises(ValueError):
            analyze_save(self.rom, self.save())
        for offset in (0xDA23C, TABLES["1.2"]-0x08000000+2):
            changed = bytearray(self.rom)
            changed[offset] ^= 1
            with self.assertRaises(ValueError):
                self.analyze(self.save(), bytes(changed))
        for size in (0, 0x1FFFF, 0x20001):
            with self.assertRaises(ValueError):
                self.analyze(bytes(size))


if __name__ == "__main__":
    unittest.main()
