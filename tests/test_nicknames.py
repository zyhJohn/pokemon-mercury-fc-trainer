import json
import unittest
from pathlib import Path

from box_data import BoxPokemon
from tests.test_box import packed_box
from tests.test_pokemon_data import sample


class NicknameTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        self.box = BoxPokemon(bytes(raw))

    def test_ten_byte_names_touch_only_nickname_in_party_and_pc(self):
        for source in [sample(), self.box]:
            for name in ["大力鳄小智", "Abc1234567", "小智Abc123"]:
                with self.subTest(source=type(source).__name__, name=name):
                    args = [self.profile] if isinstance(source, BoxPokemon) else []
                    updated, _ = source.edit(*args, nickname=name)
                    self.assertEqual(updated.nickname, name)
                    self.assertEqual(updated.raw[:8], source.raw[:8])
                    self.assertEqual(updated.raw[18:], source.raw[18:])
                    self.assertEqual(updated.ot_name, source.ot_name)

    def test_unknown_name_is_preserved_and_invalid_edit_not_truncated(self):
        for original in [sample(), self.box]:
            raw = bytearray(original.raw)
            raw[8:18] = b"\x06\x02\xff" + b"\xa5" * 7
            source = type(original)(bytes(raw))
            args = [self.profile] if isinstance(source, BoxPokemon) else []
            self.assertIsNone(source.nickname)
            unchanged, _ = source.edit(*args)
            self.assertEqual(unchanged.raw, source.raw)
            for name in ["A" * 11, "大力鳄小智龙", "", "A🐱"]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    source.edit(*args, nickname=name)
