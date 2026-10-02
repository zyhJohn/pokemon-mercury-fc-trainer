import struct
import unittest
import zlib
from types import SimpleNamespace
from sprite_images import icon_png, icon_species
from pokemon_data import change_unown_letter_pid, unown_form, shiny_value


class IconTests(unittest.TestCase):
    def test_tile_nibbles_palette_and_transparency(self):
        tiles = bytearray(512)
        tiles[0] = 0x21
        tiles[32] = 0x12
        palette = bytearray(32)
        struct.pack_into("<HH", palette, 2, 31, 31 << 5)
        png = icon_png(bytes(tiles), bytes(palette))
        offset, compressed = 8, b""
        while offset < len(png):
            n = struct.unpack_from(">I", png, offset)[0]
            tag, data = png[offset + 4 : offset + 8], png[offset + 8 : offset + 8 + n]
            self.assertEqual(
                zlib.crc32(tag + data) & 0xFFFFFFFF,
                struct.unpack_from(">I", png, offset + 8 + n)[0],
            )
            if tag == b"IDAT":
                compressed += data
            offset += n + 12
        pixels = zlib.decompress(compressed)
        self.assertEqual(pixels[1:9], bytes([255, 0, 0, 255, 0, 255, 0, 255]))
        self.assertEqual(pixels[9:13], bytes(4))
        self.assertEqual(pixels[33:41], bytes([0, 255, 0, 255, 255, 0, 0, 255]))
        with self.assertRaises(ValueError):
            icon_png(b"", bytes(palette))

    def test_egg_empty_and_unown_icon_mapping(self):
        profile = {"unown_icon_ids": [201] + list(range(413, 440))}
        mon = SimpleNamespace(species=201, egg=False, pid=0)
        self.assertEqual(icon_species(mon, profile), 201)
        mon.pid = 1
        self.assertEqual(icon_species(mon, profile), 413)
        mon.egg = True
        self.assertIsNone(icon_species(mon, profile))


class FormTests(unittest.TestCase):
    def test_all_nonshiny_natures_and_letters_keep_constraints(self):
        for nature in range(25):
            pid = 0x12345600 + nature
            self.assertGreaterEqual(shiny_value(pid, 0xABCDEF01), 8)
            for letter in range(28):
                result = change_unown_letter_pid(pid, 0xABCDEF01, letter)
                self.assertEqual(unown_form(result), letter)
                self.assertEqual(result % 25, pid % 25)
                self.assertGreaterEqual(shiny_value(result, 0xABCDEF01), 8)

    def test_shiny_form_changes_preserve_shiny_or_refuse(self):
        pid, otid = 0x12341234, 0
        self.assertLess(shiny_value(pid, otid), 8)
        accepted = 0
        for letter in range(28):
            try:
                result = change_unown_letter_pid(pid, otid, letter)
            except ValueError:
                continue
            self.assertLess(shiny_value(result, otid), 8)
            self.assertEqual(unown_form(result), letter)
            self.assertEqual(result % 25, pid % 25)
            accepted += 1
        self.assertGreater(accepted, 0)
        with self.assertRaises(ValueError):
            change_unown_letter_pid(
                pid, otid, 1 - unown_form(pid) % 2, preserve_parity=True
            )
