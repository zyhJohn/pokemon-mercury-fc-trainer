import struct
import tempfile
import unittest

from box_data import BoxPokemon
from rom_versions import load_profile
from trainer_core import Trainer, SAVE_POINTER, PARTY_COUNT
from tests.test_core import Memory
from tests.test_box import packed_box
from tests.test_rom_versions import install_profile


class BoxMoveTests(unittest.TestCase):
    def setup_release(self, crc, folder):
        profile = load_profile(crc=crc)
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\0")
        return memory, Trainer(memory, profile, folder), profile

    def test_move_exact_opaque_record_across_noncontiguous_boxes_and_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc), tempfile.TemporaryDirectory() as folder:
                memory, trainer, profile = self.setup_release(crc, folder)
                source, target = profile["storage"]["box_addresses"][18:20]
                raw = bytearray(packed_box())
                raw[18] = 0xA5  # Preserve unknown data, including untouched moves.
                raw = bytes(raw)
                memory.put(source + 29 * 58, raw)
                memory.put(target, packed_box())
                result = trainer.commit_box_move(
                    trainer.snapshot_box(18), 29, trainer.snapshot_box(19)
                )
                self.assertEqual(result["target_slot"], 1)
                self.assertEqual(memory.read(source + 29 * 58, 58), b"\0" * 58)
                self.assertEqual(memory.read(target + 58, 58), raw)
                self.assertEqual(memory.read(target, 58), packed_box())
                trainer.restore(result["backup"])
                self.assertEqual(memory.read(source + 29 * 58, 58), raw)
                self.assertEqual(memory.read(target + 58, 58), b"\0" * 58)

    def test_empty_same_box_full_and_unknown_empty_data_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, profile = self.setup_release("4755f497", folder)
            source, target = profile["storage"]["box_addresses"][:2]
            memory.put(source, packed_box())
            src = trainer.snapshot_box(0)
            with self.assertRaises(ValueError):
                trainer.prepare_box_move(src, 1, trainer.snapshot_box(1))
            with self.assertRaises(ValueError):
                trainer.prepare_box_move(src, 0, src)
            memory.put(target, packed_box() * 30)
            with self.assertRaises(ValueError):
                trainer.prepare_box_move(src, 0, trainer.snapshot_box(1))
            memory.put(target, b"\xa5" + b"\0" * 57)
            with self.assertRaises(ValueError):
                trainer.prepare_box_move(src, 0, trainer.snapshot_box(1), 0)
            self.assertEqual(memory.writes, 0)

    def test_stale_source_or_target_never_duplicates_or_loses_mon(self):
        for change_source in (True, False):
            with (
                self.subTest(source=change_source),
                tempfile.TemporaryDirectory() as folder,
            ):
                memory, trainer, profile = self.setup_release("4755f497", folder)
                source, target = profile["storage"]["box_addresses"][:2]
                memory.put(source, packed_box())
                src, dst = trainer.snapshot_box(0), trainer.snapshot_box(1)
                if change_source:
                    memory.put(source + 18, b"\xa5")
                else:
                    memory.put(target, packed_box())
                before = dict(memory.data)
                with self.assertRaises(OSError):
                    trainer.commit_box_move(src, 0, dst)
                self.assertEqual(memory.data, before)
                self.assertEqual(memory.writes, 0)

    def test_restore_requires_both_slots_still_match(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, profile = self.setup_release("4755f497", folder)
            source, target = profile["storage"]["box_addresses"][:2]
            memory.put(source, packed_box())
            result = trainer.commit_box_move(
                trainer.snapshot_box(0), 0, trainer.snapshot_box(1)
            )
            memory.put(source, packed_box())  # Game reused the original empty slot.
            before = dict(memory.data)
            with self.assertRaises(OSError):
                trainer.restore(result["backup"])
            self.assertEqual(memory.data, before)
            self.assertEqual(BoxPokemon(memory.read(target, 58)).species, 160)
