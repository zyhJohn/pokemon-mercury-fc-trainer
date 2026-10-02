import json
import struct
import unittest
from pathlib import Path

from box_data import BoxPokemon
from pokemon_data import Pokemon, change_nature_pid, toxtricity_species
from tests.test_box import packed_box
from tests.test_pokemon_data import sample
from trainer_core import Trainer


class ToxtricityTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.trainer = Trainer(None, self.profile, "unused")
        patches, _ = self.trainer.edit_pokemon(
            {"party": [sample(flags=0)]}, 0, species=1141, nature=0, ability_slot=1
        )
        raw = bytearray(patches[0][2])
        struct.pack_into("<I", raw, 36, struct.unpack_from("<I", raw, 36)[0] + 5)
        self.party = Pokemon(bytes(raw))
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        struct.pack_into("<H", raw, 28, 1141)
        struct.pack_into("<I", raw, 0, self.party.pid)
        struct.pack_into(
            "<I", raw, 54, int.from_bytes(raw[54:58], "little") & 0x7FFFFFFF
        )
        self.box = BoxPokemon(bytes(raw))

    def test_nature_changes_sync_both_forms_and_preserve_experience_and_slots(self):
        for nature in range(25):
            for source in [self.party, self.box]:
                with self.subTest(nature=nature, source=type(source).__name__):
                    if isinstance(source, Pokemon):
                        patches, report = self.trainer.edit_pokemon(
                            {"party": [source]}, 0, nature=nature
                        )
                        updated = Pokemon(patches[0][2])
                        self.assertEqual(updated.raw[4:32], source.raw[4:32])
                    else:
                        updated, report = source.edit(self.profile, nature=nature)
                        allowed = set(range(4)) | {28, 29}
                        self.assertTrue(
                            all(
                                updated.raw[i] == source.raw[i]
                                for i in range(58)
                                if i not in allowed
                            )
                        )
                    self.assertEqual(updated.species, toxtricity_species(nature))
                    self.assertEqual(updated.pid % 25, nature)
                    self.assertEqual(updated.experience, source.experience)
                    self.assertEqual(updated.pid & 255, source.pid & 255)
                    self.assertEqual(updated.shiny, source.shiny)
                    self.assertEqual(updated.ability_flag, source.ability_flag)
                    self.assertFalse(report["errors"])
        patches, report = self.trainer.edit_pokemon(
            {"party": [self.party]}, 0, nature=1
        )
        low = Pokemon(patches[0][2])
        self.assertEqual(report["ability_slot"], 1)
        self.assertEqual(self.profile["species"][str(low.species)]["abilities"][1], 58)

    def test_conflicting_explicit_form_and_pending_reversion_refused(self):
        with self.assertRaisesRegex(ValueError, "由性格决定"):
            self.trainer.edit_pokemon({"party": [self.party]}, 0, species=1193)
        raw = bytearray(self.party.raw)
        struct.pack_into("<H", raw, 28, 160)
        with self.assertRaisesRegex(ValueError, "待还原"):
            self.trainer.edit_pokemon({"party": [Pokemon(bytes(raw))]}, 0, nature=1)

    def test_existing_mismatched_form_preserved_without_nature_change(self):
        raw = bytearray(self.party.raw)
        struct.pack_into(
            "<I", raw, 0, change_nature_pid(self.party.pid, self.party.otid, 1, 1141)
        )
        mon = Pokemon(bytes(raw))
        patches, report = self.trainer.edit_pokemon(
            {"party": [mon]}, 0, nature=1, shiny=True
        )
        self.assertEqual(Pokemon(patches[0][2]).species, 1141)
        self.assertTrue(any("不匹配" in note for note in report["notes"]))
        raw = bytearray(self.box.raw)
        struct.pack_into(
            "<I", raw, 0, change_nature_pid(self.box.pid, self.box.otid, 1, 1141)
        )
        box = BoxPokemon(bytes(raw))
        updated, _ = box.edit(self.profile, nature=1, shiny=True)
        self.assertEqual(updated.species, 1141)

    def test_egg_form_change_retains_flags_and_one_level_experience(self):
        patches, _ = self.trainer.edit_pokemon({"party": [self.party]}, 0, egg=True)
        egg = Pokemon(patches[0][2])
        patches, _ = self.trainer.edit_pokemon(
            {"party": [egg]}, 0, nature=1, shiny=True
        )
        updated = Pokemon(patches[0][2])
        self.assertEqual(updated.species, 1193)
        self.assertEqual(
            (updated.egg, updated.level, updated.experience), (True, 1, egg.experience)
        )
        self.assertEqual(updated.raw[19], egg.raw[19])
        self.assertEqual(updated.raw[72:76], egg.raw[72:76])
        pc_egg, _ = self.box.edit(self.profile, egg=True)
        pc_updated, _ = pc_egg.edit(self.profile, nature=1, shiny=True)
        self.assertEqual(pc_updated.species, 1193)
        self.assertEqual(
            (pc_updated.egg, pc_updated.experience), (True, pc_egg.experience)
        )
        self.assertEqual(pc_updated.raw[54:58], pc_egg.raw[54:58])
