import json
import struct
import unittest
from pathlib import Path

from box_data import BoxPokemon
from pokemon_data import Pokemon
from tests.test_box import packed_box
from tests.test_pokemon_data import sample
from trainer_core import Trainer


class HeldFormTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.trainer = Trainer(None, self.profile, "unused")

    def sources(self, species):
        patches, _ = self.trainer.edit_pokemon(
            {"party": [sample()]}, 0, species=species
        )
        raw = bytearray(patches[0][2])
        struct.pack_into("<I", raw, 36, struct.unpack_from("<I", raw, 36)[0] + 5)
        party = Pokemon(bytes(raw))
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        struct.pack_into("<H", raw, 28, species)
        struct.pack_into("<I", raw, 32, party.experience)
        return party, BoxPokemon(bytes(raw))

    def test_six_family_item_changes_sync_species_and_preserve_progress(self):
        for species, item, target in [
            (546, 490, 720),
            (990, 507, 1048),
            (540, 489, 718),
            (702, 524, 748),
            (536, 487, 919),
            (537, 488, 920),
        ]:
            party, pc = self.sources(species)
            for source in [party, pc]:
                with self.subTest(species=species, source=type(source).__name__):
                    if isinstance(source, Pokemon):
                        patches, _ = self.trainer.edit_pokemon(
                            {"party": [source]}, 0, held=item
                        )
                        updated = Pokemon(patches[0][2])
                        allowed = {*range(32, 36), *range(86, 100)}
                        patches, _ = self.trainer.edit_pokemon(
                            {"party": [updated]}, 0, held=0
                        )
                        reverted = Pokemon(patches[0][2])
                    else:
                        updated, _ = source.edit(self.profile, held=item)
                        allowed = set(range(28, 32))
                        reverted, _ = updated.edit(self.profile, held=0)
                    self.assertEqual((updated.species, updated.held), (target, item))
                    self.assertEqual(updated.experience, source.experience)
                    self.assertEqual(updated.pid, source.pid)
                    self.assertEqual(updated.ability_flag, source.ability_flag)
                    self.assertTrue(
                        all(
                            updated.raw[i] == source.raw[i]
                            for i in range(len(source.raw))
                            if i not in allowed
                        )
                    )
                    self.assertEqual(reverted.raw, source.raw)

    def test_conflicting_explicit_form_and_pending_reversion_refused(self):
        party, _ = self.sources(546)
        with self.assertRaisesRegex(ValueError, "携带道具不一致"):
            self.trainer.edit_pokemon({"party": [party]}, 0, species=720)
        patches, _ = self.trainer.edit_pokemon(
            {"party": [party]}, 0, species=720, held=490
        )
        self.assertEqual(Pokemon(patches[0][2]).species, 720)
        raw = bytearray(party.raw)
        struct.pack_into("<H", raw, 28, 257)
        with self.assertRaisesRegex(ValueError, "待还原"):
            self.trainer.edit_pokemon({"party": [Pokemon(bytes(raw))]}, 0, held=490)

    def test_existing_mismatch_preserved_until_item_changes(self):
        party, pc = self.sources(546)
        raw = bytearray(party.raw)
        struct.pack_into("<H", raw, 32, 720)
        party = Pokemon(bytes(raw))
        patches, report = self.trainer.edit_pokemon({"party": [party]}, 0, held=0)
        self.assertEqual(Pokemon(patches[0][2]).species, 720)
        self.assertTrue(any("已保存形态不同" in note for note in report["notes"]))
        raw = bytearray(pc.raw)
        struct.pack_into("<H", raw, 28, 720)
        pc = BoxPokemon(bytes(raw))
        unchanged, _ = pc.edit(self.profile, held=0)
        self.assertEqual(unchanged.raw, pc.raw)
        updated, _ = pc.edit(self.profile, held=13)
        self.assertEqual(updated.species, 546)

    def test_pc_item_validation_and_generic_item_bytes(self):
        _, pc = self.sources(546)
        for item in [365, 341, 750, True]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                pc.edit(self.profile, held=item)
        raw = bytearray(pc.raw)
        struct.pack_into("<H", raw, 28, 160)
        pc = BoxPokemon(bytes(raw))
        updated, _ = pc.edit(self.profile, held=13)
        expected = bytearray(pc.raw)
        struct.pack_into("<H", expected, 30, 13)
        self.assertEqual(updated.raw, bytes(expected))

    def test_special_z_crystals_do_not_select_arceus_elemental_form(self):
        party, pc = self.sources(546)
        for item, target in [(581, 720), (598, 546), (532, 546)]:
            patches, _ = self.trainer.edit_pokemon({"party": [party]}, 0, held=item)
            updated = Pokemon(patches[0][2])
            self.assertEqual(updated.species, target)
            box, _ = pc.edit(self.profile, held=item)
            self.assertEqual(box.species, target)

    def test_egg_flags_and_one_level_progress_preserved_during_held_form_change(self):
        party, pc = self.sources(546)
        patches, _ = self.trainer.edit_pokemon({"party": [party]}, 0, egg=True)
        egg = Pokemon(patches[0][2])
        patches, _ = self.trainer.edit_pokemon({"party": [egg]}, 0, held=490)
        updated = Pokemon(patches[0][2])
        self.assertEqual((updated.species, updated.level, updated.egg), (720, 1, True))
        self.assertEqual(updated.experience, egg.experience)
        self.assertEqual(updated.raw[19], egg.raw[19])
        self.assertEqual(updated.raw[72:76], egg.raw[72:76])
        pc_egg, _ = pc.edit(self.profile, egg=True)
        box, _ = pc_egg.edit(self.profile, held=490)
        self.assertEqual((box.species, box.egg), (720, True))
        self.assertEqual(box.experience, pc_egg.experience)
        self.assertEqual(box.raw[54:58], pc_egg.raw[54:58])
