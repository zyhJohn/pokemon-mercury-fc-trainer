import unittest
from name_codec import encode_name, decode_name
from pokemon_data import Pokemon
from tests.test_pokemon_data import sample
from tests import test_details


class NameTests(unittest.TestCase):
    def test_codec_bounds_and_unknown_encoding(self):
        self.assertEqual(encode_name("zyh", 8), b"\xee\xed\xdc" + b"\xff" * 5)
        for text in ["Abc1234", "Red", "A-b 'Z"]:
            self.assertEqual(decode_name(encode_name(text, 8)), text)
        self.assertIsNone(decode_name(b"\x01\x02\xff"))
        for text in ["", "       ", "ABCDEFGH", "中文", "A\nB", "é"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                encode_name(text, 8)

    def test_ot_name_edit_touches_only_seven_name_bytes(self):
        mon = sample()
        updated, _ = mon.edit(ot_name="Abc1234")
        self.assertEqual(updated.ot_name, "Abc1234")
        self.assertEqual(updated.raw[:20], mon.raw[:20])
        self.assertEqual(updated.raw[27:], mon.raw[27:])
        raw = bytearray(mon.raw)
        raw[20] = 1
        unknown = Pokemon(bytes(raw))
        self.assertIsNone(unknown.ot_name)
        same, _ = unknown.edit()
        self.assertEqual(same.raw, unknown.raw)


class PlayerNameTests(unittest.TestCase):
    def setUp(self):
        test_details.TrainerIdentityTests.setUp(self)

    def test_player_name_and_ids_restore_with_unknown_name_bytes(self):
        self.mem.put(self.address, b"\x01\x02" + self.raw[2:])
        before = self.mem.read(self.address, 14)
        snap = self.trainer.snapshot_trainer()
        self.assertIsNone(snap["name"])
        result = self.trainer.commit_trainer_profile(snap, 65535, 65535, "Abc1234")
        self.assertEqual(self.trainer.snapshot_trainer()["name"], "Abc1234")
        self.assertEqual(self.mem.read(self.address + 8, 2), before[8:10])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.read(self.address, 14), before)
