import json
import unittest
from pathlib import Path

from box_data import BoxPokemon
from pokemon_data import Pokemon
from tests.test_box import packed_box
from tests.test_pokemon_data import sample
from trainer_core import Trainer


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.trainer = Trainer(None, self.profile, "unused")

    def test_new_invalid_pointer_id_refused_but_existing_unknown_is_preserved(self):
        mon = sample()
        for ident in [222, 249, 252]:
            with (
                self.subTest(ident=ident),
                self.assertRaisesRegex(ValueError, "无效名称指针"),
            ):
                self.trainer.edit_pokemon({"party": [mon]}, 0, met_location=ident)
        raw = bytearray(mon.raw)
        raw[69] = 222
        unknown = Pokemon(bytes(raw))
        patches, _ = self.trainer.edit_pokemon(
            {"party": [unknown]}, 0, met_location=222, ivs=[31] * 6
        )
        self.assertEqual(Pokemon(patches[0][2]).met_location, 222)
        patches, _ = self.trainer.edit_pokemon(
            {"party": [unknown]}, 0, met_location=213
        )
        self.assertEqual(Pokemon(patches[0][2]).met_location, 213)

    def test_pc_location_guard_and_known_label_mapping(self):
        self.assertEqual(self.profile["met_locations"]["88"], "真新镇")
        self.assertEqual(self.profile["met_locations"]["143"], "若叶镇")
        self.assertEqual(self.profile["met_locations"]["213"], "跨海大桥")
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        raw[51] = 222
        mon = BoxPokemon(bytes(raw))
        snapshot = {
            "index": 0,
            "address": self.profile["storage"]["box_addresses"][0],
            "pokemon": [mon],
        }
        with self.assertRaisesRegex(ValueError, "无效名称指针"):
            self.trainer.edit_box(snapshot, 0, met_location=223)
        patches, _ = self.trainer.edit_box(snapshot, 0, met_location=222)
        self.assertEqual(patches[0][1], patches[0][2])
        patches, _ = self.trainer.edit_box(snapshot, 0, met_location=143)
        expected = bytearray(mon.raw)
        expected[51] = 143
        self.assertEqual(patches[0][2], bytes(expected))
