import unittest

from name_codec import CHINESE_ENCODE, decode_name, encode_name
from pokemon_data import Pokemon
from tests import test_details
from tests.test_pokemon_data import sample


class NameTests(unittest.TestCase):
    def test_codec_bounds_and_unknown_encoding(self):
        self.assertEqual(encode_name("zyh", 8), b"\xee\xed\xdc" + b"\xff" * 5)
        for text in ["Abc1234", "Red", "A-b 'Z"]:
            self.assertEqual(decode_name(encode_name(text, 8)), text)
        self.assertIsNone(decode_name(b"\x06\x02\xff"))
        for text in ["", "       ", "ABCDEFGH", "小智皮卡", "A\nB", "é", "😀"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                encode_name(text, 8)

    def test_chinese_species_bytes_mixed_capacity_and_invalid_sequences(self):
        self.assertEqual(encode_name("大力鳄", 7), bytes.fromhex("02d308791df1ff"))
        self.assertEqual(decode_name(bytes.fromhex("09860d4410a7110bff")), "妙蛙种子")
        for text in ["小智", "小智A12", "阿B明2", "大力鳄A"]:
            self.assertEqual(decode_name(encode_name(text, 7)), text)
        for text in ["小智AB12", "小智皮卡"]:
            with self.assertRaisesRegex(ValueError, "字节"):
                encode_name(text, 8)
        self.assertEqual(len(CHINESE_ENCODE), 6763)
        for raw in [b"\x01\xff", b"\x01\xf7", b"\x1e\x5e", b"\x1b\xff"]:
            self.assertIsNone(decode_name(raw))

    def test_pc_and_party_chinese_ot_names_preserve_other_bytes(self):
        import json
        from pathlib import Path

        from box_data import BoxPokemon
        from tests.test_box import packed_box

        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        for mon in [sample(), BoxPokemon(packed_box())]:
            if isinstance(mon, BoxPokemon):
                # The synthetic fixture has an intentionally unknown move 1023.
                raw = bytearray(mon.raw)
                raw[39:44] = (757).to_bytes(5, "little")
                mon = BoxPokemon(bytes(raw))
                updated, _ = mon.edit(profile, ot_name="大力鳄A")
            else:
                updated, _ = mon.edit(ot_name="大力鳄A")
            self.assertEqual(updated.ot_name, "大力鳄A")
            self.assertEqual(updated.raw[:20], mon.raw[:20])
            self.assertEqual(updated.raw[27:], mon.raw[27:])

    def test_ot_name_edit_touches_only_seven_name_bytes(self):
        mon = sample()
        updated, _ = mon.edit(ot_name="Abc1234")
        self.assertEqual(updated.ot_name, "Abc1234")
        self.assertEqual(updated.raw[:20], mon.raw[:20])
        self.assertEqual(updated.raw[27:], mon.raw[27:])
        raw = bytearray(mon.raw)
        raw[20] = 6
        unknown = Pokemon(bytes(raw))
        self.assertIsNone(unknown.ot_name)
        same, _ = unknown.edit()
        self.assertEqual(same.raw, unknown.raw)


class PlayerNameTests(unittest.TestCase):
    def setUp(self):
        test_details.TrainerIdentityTests.setUp(self)

    def test_player_name_and_ids_restore_with_unknown_name_bytes(self):
        self.mem.put(self.address, b"\x06\x02" + self.raw[2:])
        before = self.mem.read(self.address, 14)
        snap = self.trainer.snapshot_trainer()
        self.assertIsNone(snap["name"])
        result = self.trainer.commit_trainer_profile(snap, 65535, 65535, "Abc1234")
        self.assertEqual(self.trainer.snapshot_trainer()["name"], "Abc1234")
        self.assertEqual(self.mem.read(self.address + 8, 2), before[8:10])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.read(self.address, 14), before)

    def test_chinese_player_name_preserves_ids_gender_and_restores(self):
        snapshot = self.trainer.snapshot_trainer()
        result = self.trainer.commit_trainer_profile(
            snapshot, snapshot["tid"], snapshot["sid"], "大力鳄A"
        )
        self.assertEqual(self.trainer.snapshot_trainer()["name"], "大力鳄A")
        self.assertEqual(self.mem.read(self.address + 8, 6), snapshot["raw"][8:])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.read(self.address, 14), snapshot["raw"])
