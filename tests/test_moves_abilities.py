import unittest
import json
from pathlib import Path
from pokemon_data import Pokemon, gender
from tests.test_pokemon_data import sample


class MoveAbilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))

    def test_hidden_ability_preserves_iv_and_egg(self):
        for flag in [0, 0x40000000]:
            mon = sample(flags=flag)
            edited, _ = mon.edit(
                ability_slot=2, abilities=[67, 89, 125], gender_ratio=31
            )
            self.assertEqual(edited.ivs, mon.ivs)
            self.assertEqual(edited.egg, mon.egg)
            self.assertEqual(edited.ability_flag, 1)
            self.assertEqual(edited.pid, mon.pid)

    def test_switch_normal_ability_preserves_gender_nature_shiny(self):
        for ratio in [0, 31, 127, 254, 255]:
            for shiny in [False, True]:
                original = sample(flags=0)
                mon, _ = original.edit(shiny=shiny)
                edited, _ = mon.edit(
                    ability_slot=1 - (mon.pid & 1),
                    abilities=[67, 89, 125],
                    gender_ratio=ratio,
                )
                self.assertNotEqual(edited.pid & 1, mon.pid & 1)
                self.assertEqual(gender(edited.pid, ratio), gender(mon.pid, ratio))
                self.assertEqual(edited.pid % 25, mon.pid % 25)
                self.assertEqual(edited.shiny, shiny)
                self.assertEqual(edited.ivs, mon.ivs)

    def test_missing_ability_refused(self):
        with self.assertRaises(ValueError):
            sample().edit(ability_slot=2, abilities=[67, 0, 0], gender_ratio=31)

    def test_move_change_resets_only_that_pp_bonus(self):
        data = bytearray(sample().raw)
        data[40] = 0xFF
        mon = Pokemon(bytes(data))
        edited, _ = mon.edit(
            moves=[57, 242, 8, 700],
            pp=[15, 15, 20, 10],
            move_data=self.profile["moves"],
        )
        self.assertEqual(edited.raw[40], 0xFC)
        self.assertEqual(edited.moves, (57, 242, 8, 700))
        self.assertEqual(edited.raw[56:], mon.raw[56:])
        self.assertEqual(edited.raw[:40], mon.raw[:40])

    def test_invalid_move_pp_duplicate_and_empty_refused(self):
        for moves, pp in [
            ([57, 57, 8, 700], [1] * 4),
            ([57, 242, 8, 700], [16, 1, 1, 1]),
            ([0, 0, 0, 0], [0] * 4),
            ([65535, 0, 0, 0], [0] * 4),
        ]:
            with self.subTest(moves=moves, pp=pp):
                with self.assertRaises(ValueError):
                    sample().edit(moves=moves, pp=pp, move_data=self.profile["moves"])

    def test_empty_moves_normalize_pp_and_bonus_and_allow_egg_conversion(self):
        raw = bytearray(sample().raw)
        raw[40] = 255
        edited, _ = Pokemon(bytes(raw)).edit(
            moves=[57, 0, 0, 0], pp=[1, 99, 99, 99], move_data=self.profile["moves"]
        )
        self.assertEqual(edited.pp, (1, 0, 0, 0))
        self.assertEqual(edited.raw[40], 0)
        metadata = self.profile["species"][str(sample().species)]
        edited, _ = sample().edit(
            egg=True,
            egg_cycles=20,
            moves=[0] * 4,
            pp=[99] * 4,
            move_data=self.profile["moves"],
            base=metadata["base"],
            growth=metadata["growth"],
            experience_tables=self.profile["experience_tables"],
        )
        self.assertTrue(edited.egg)
        self.assertEqual(edited.pp, (0,) * 4)

    def test_combined_shiny_iv_ev_ability_edit(self):
        mon = sample()
        edited, _ = mon.edit(
            shiny=True,
            ivs=[31] * 6,
            evs=[4, 252, 0, 252, 0, 0],
            ability_slot=1,
            abilities=[67, 89, 125],
            gender_ratio=31,
            base=[85, 105, 100, 78, 79, 83],
        )
        self.assertTrue(edited.shiny)
        self.assertEqual(edited.pid & 1, 1)
        self.assertEqual(edited.pid % 25, mon.pid % 25)
        self.assertEqual(edited.ivs, (31,) * 6)
        self.assertEqual(edited.raw[28:32], mon.raw[28:32])


if __name__ == "__main__":
    unittest.main()
