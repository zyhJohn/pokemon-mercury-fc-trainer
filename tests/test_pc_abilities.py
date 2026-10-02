import json
import struct
import unittest
from pathlib import Path

from box_data import BoxPokemon
from pokemon_data import Pokemon, gender, unown_form
from tests.test_box import packed_box
from tests.test_pokemon_data import sample
from trainer_core import Trainer


class PCAbilityTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.trainer = Trainer(None, self.profile, "unused")

    def source(self, species=133, pid=0x12345678, egg=False):
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        struct.pack_into("<H", raw, 28, species)
        struct.pack_into("<I", raw, 0, pid)
        raw[57] &= 127
        mon = BoxPokemon(bytes(raw))
        if egg:
            mon, _ = mon.edit(self.profile, egg=True)
        return mon

    def test_slot_selection_preserves_shiny_nature_gender_and_egg_iv_evs(self):
        for egg in [False, True]:
            source = self.source(egg=egg)
            for shiny in [False, True]:
                for slot in [0, 1, 2]:
                    with self.subTest(egg=egg, shiny=shiny, slot=slot):
                        updated, report = source.edit(
                            self.profile,
                            ability_slot=slot,
                            shiny=shiny,
                            nature=7,
                            ivs=[31] * 6,
                        )
                        ratio = self.profile["species"]["133"]["gender_ratio"]
                        self.assertEqual(
                            (updated.egg, updated.shiny, updated.pid % 25),
                            (egg, shiny, 7),
                        )
                        self.assertEqual(
                            gender(updated.pid, ratio), gender(source.pid, ratio)
                        )
                        self.assertEqual(updated.ivs, (31,) * 6)
                        self.assertEqual(updated.evs, source.evs)
                        self.assertEqual(updated.ability_flag, int(slot == 2))
                        if slot < 2:
                            self.assertEqual(updated.pid & 1, slot)
                        self.assertEqual(
                            report["ability"],
                            self.profile["species"]["133"]["abilities"][slot],
                        )

    def test_single_ordinary_slot_preserves_odd_pid_and_unown_letter(self):
        for species in [201, 546, 990, 702]:
            source = self.source(species, 0x12345679)
            updated, _ = source.edit(self.profile, ability_slot=0)
            self.assertEqual(updated.pid, source.pid)
            self.assertEqual(unown_form(updated.pid), unown_form(source.pid))
        patches, _ = self.trainer.edit_pokemon({"party": [sample()]}, 0, species=546)
        party = Pokemon(patches[0][2])
        patches, _ = self.trainer.edit_pokemon({"party": [party]}, 0, ability_slot=0)
        self.assertEqual(Pokemon(patches[0][2]).pid, party.pid)

    def test_spinda_ordinary_change_requires_explicit_pattern_seed(self):
        source = self.source(308)
        with self.assertRaisesRegex(ValueError, "花纹"):
            source.edit(self.profile, ability_slot=1)
        updated, _ = source.edit(self.profile, ability_slot=1, spinda_seed=0x12345678)
        self.assertEqual(updated.pid & 1, 1)
        self.assertNotEqual(updated.pid, source.pid)
        self.assertEqual(updated.pid % 25, source.pid % 25)
        hidden, _ = source.edit(self.profile, ability_slot=2)
        self.assertEqual(hidden.pid, source.pid)
        self.assertEqual(hidden.ability_flag, 1)

    def test_missing_target_slot_and_invalid_inputs_refused(self):
        source = self.source(540)
        with self.assertRaisesRegex(ValueError, "目标形态"):
            source.edit(self.profile, ability_slot=2, held=489)
        for slot in [1, 3, True, "invalid"]:
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                self.source(546).edit(self.profile, ability_slot=slot)
