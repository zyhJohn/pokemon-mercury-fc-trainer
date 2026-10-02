import json
import struct
import unittest
from pathlib import Path
from pokemon_data import (
    regenerate_spinda_pid,
    shiny_value,
    gender,
    Pokemon,
    experience_for_level,
)
from spinda_images import lz77
from trainer_core import Trainer
from tests.test_pokemon_data import sample
from tests.test_core import Memory
from box_data import BoxPokemon
from tests.test_box import packed_box


class SpindaTests(unittest.TestCase):
    def test_lz77_overlap_literals_bad_headers_and_backreferences(self):
        self.assertEqual(lz77(b"\x10\x06\0\0\x40A\x20\0", 6), b"AAAAAA")
        self.assertEqual(lz77(b"\x10\x03\0\0\0abc", 3), b"abc")
        for data in [b"", b"\x10\x06\0\0\x80\x20\0", b"\x10\x06\0\0\0a"]:
            with self.assertRaises(ValueError):
                lz77(data, 6)

    def test_regeneration_preserves_requested_constraints_and_changes_pid(self):
        for pid, otid in [(0x12345678, 0xABCDEF01), (0x12341234, 0)]:
            for nature in range(25):
                for shiny in [False, True]:
                    result = regenerate_spinda_pid(
                        pid, otid, 0x12345678, nature, shiny, 127, pid & 1
                    )
                    self.assertNotEqual(result, pid)
                    self.assertEqual(result % 25, nature)
                    self.assertEqual(shiny_value(result, otid) < 8, shiny)
                    self.assertEqual(gender(result, 127), gender(pid, 127))
                    self.assertEqual(result & 1, pid & 1)
                    self.assertNotEqual(result & 240, 0)

    def test_party_and_pc_require_explicit_regeneration(self):
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        raw = bytearray(sample().raw)
        struct.pack_into("<H", raw, 32, 308)
        struct.pack_into(
            "<I",
            raw,
            36,
            experience_for_level(
                raw[84],
                profile["species"]["308"]["growth"],
                profile["experience_tables"],
            ),
        )
        mon = Pokemon(bytes(raw))
        trainer = Trainer(Memory(), profile, "backups")
        snapshot = {"party": [mon]}
        with self.assertRaisesRegex(ValueError, "PID 花纹"):
            trainer.edit_pokemon(snapshot, 0, shiny=not mon.shiny)
        patches, _ = trainer.edit_pokemon(
            snapshot, 0, spinda_seed=0, shiny=not mon.shiny, nature=3
        )
        updated = Pokemon(patches[0][2])
        self.assertEqual(updated.shiny, not mon.shiny)
        self.assertEqual(updated.pid % 25, 3)
        self.assertEqual(updated.evs, mon.evs)
        raw = bytearray(packed_box())
        struct.pack_into("<H", raw, 28, 308)
        raw[39:44] = (757).to_bytes(5, "little")
        box = BoxPokemon(bytes(raw))
        updated_box, _ = box.edit(profile, spinda_seed=0, shiny=not box.shiny, nature=3)
        self.assertEqual(updated_box.shiny, not box.shiny)
        self.assertEqual(updated_box.pid % 25, 3)
        self.assertEqual(updated_box.raw[4:], box.raw[4:])
        with self.assertRaises(ValueError):
            sample().edit(spinda_seed=0)


if __name__ == "__main__":
    unittest.main()
