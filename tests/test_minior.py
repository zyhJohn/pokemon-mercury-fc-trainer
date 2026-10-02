import json
import struct
import unittest
from pathlib import Path

from box_data import BoxPokemon
from pokemon_data import (
    MINIOR_CORES,
    Pokemon,
    change_minior_color_pid,
    change_nature_pid,
    change_shiny_pid,
    shiny_value,
)
from tests.test_box import packed_box
from tests.test_pokemon_data import sample
from trainer_core import Trainer


class MiniorTests(unittest.TestCase):
    def test_color_changes_and_ordinary_pid_edits_keep_constraints(self):
        for otid, initial in [(0, 0), (0x40F88D36, 0x43DEB981)]:
            for shiny in [False, True]:
                pid = change_shiny_pid(initial, otid, shiny, 1065)
                for color in range(7):
                    updated = change_minior_color_pid(pid, otid, color)
                    self.assertEqual(updated % 7, color)
                    self.assertEqual(updated % 25, pid % 25)
                    self.assertEqual(updated & 255, pid & 255)
                    self.assertEqual(shiny_value(updated, otid) < 8, shiny)
                    nature = change_nature_pid(
                        updated, otid, (updated % 25 + 1) % 25, 1065
                    )
                    self.assertEqual(nature % 7, color)
                    toggled = change_shiny_pid(nature, otid, not shiny, 1065)
                    self.assertEqual(toggled % 7, color)
                    self.assertEqual(shiny_value(toggled, otid) < 8, not shiny)

    def test_party_and_pc_set_core_species_and_preserve_other_fields(self):
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        trainer = Trainer(None, profile, "unused")
        party_raw = bytearray(
            trainer.edit_pokemon({"party": [sample()]}, 0, species=991)[0][0][2]
        )
        struct.pack_into(
            "<I", party_raw, 36, struct.unpack_from("<I", party_raw, 36)[0] + 5
        )
        party = Pokemon(bytes(party_raw))
        raw = bytearray(packed_box())
        struct.pack_into("<H", raw, 28, 991)
        raw[39:44] = (757).to_bytes(5, "little")
        box = BoxPokemon(bytes(raw))
        for color, species in enumerate(MINIOR_CORES):
            patches, _ = trainer.edit_pokemon({"party": [party]}, 0, minior_color=color)
            updated = Pokemon(patches[0][2])
            self.assertEqual((updated.species, updated.pid % 7), (species, color))
            self.assertEqual(updated.raw[4:32], party.raw[4:32])
            self.assertEqual(updated.experience, party.experience)
            self.assertEqual(
                (updated.ivs, updated.evs, updated.shiny),
                (party.ivs, party.evs, party.shiny),
            )
            pc, _ = box.edit(profile, minior_color=color)
            self.assertEqual((pc.species, pc.pid % 7), (species, color))
            allowed = set(range(4)) | {28, 29}
            self.assertTrue(
                all(pc.raw[i] == box.raw[i] for i in range(58) if i not in allowed)
            )
        with self.assertRaisesRegex(ValueError, "小陨星"):
            sample().edit(minior_color=0)
        with self.assertRaises(ValueError):
            party.edit(minior_color=7)

    def test_pending_form_reversion_blocks_species_change(self):
        raw = bytearray(sample().raw)
        struct.pack_into("<H", raw, 28, 257)
        mon = Pokemon(bytes(raw))
        with self.assertRaisesRegex(ValueError, "待还原"):
            mon.edit(species=281)
        updated, _ = mon.edit(ivs=[31] * 6, base=[85, 105, 100, 78, 79, 83])
        self.assertEqual(updated.raw[28:32], mon.raw[28:32])
