import struct
import tempfile
import unittest
import json
from pathlib import Path

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

    def test_interrupted_move_keeps_both_original_slots_in_unconfirmed_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, profile = self.setup_release("4755f497", folder)
            source, target = profile["storage"]["box_addresses"][:2]
            original = packed_box()
            memory.put(source, original)

            def interrupted(patches, verify=False):
                # Simulate loss of the bridge response after only the source
                # has been cleared. A callback failure is not an atomic undo.
                memory.put(source, b"\0" * 58)
                memory.writes += 1
                raise OSError("connection lost during write")

            memory.batch = interrupted
            with self.assertRaisesRegex(OSError, "connection lost"):
                trainer.commit_box_move(
                    trainer.snapshot_box(0), 0, trainer.snapshot_box(1)
                )
            path = next(Path(folder).glob("*.json"))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed-or-unconfirmed")
            self.assertEqual(len(record["patches"]), 2)
            self.assertEqual(bytes.fromhex(record["patches"][0]["before"]), original)
            self.assertEqual(bytes.fromhex(record["patches"][1]["before"]), b"\0" * 58)
            self.assertEqual(memory.read(target, 58), b"\0" * 58)
            with self.assertRaisesRegex(ValueError, "未被确认"):
                trainer.restore(path)
            self.assertEqual(memory.writes, 1)  # No retry or speculative undo.

    def test_move_readback_failure_preserves_backup_and_refuses_automatic_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, profile = self.setup_release("4755f497", folder)
            source, target = profile["storage"]["box_addresses"][:2]
            original = packed_box()
            memory.put(source, original)
            batch = memory.batch

            def changed_after_write(patches, verify=False):
                batch(patches, verify)
                memory.put(target + 18, b"\xa5")

            memory.batch = changed_after_write
            with self.assertRaisesRegex(OSError, "读回不一致"):
                trainer.commit_box_move(
                    trainer.snapshot_box(0), 0, trainer.snapshot_box(1)
                )
            path = next(Path(folder).glob("*.json"))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed-or-unconfirmed")
            self.assertEqual(bytes.fromhex(record["patches"][0]["before"]), original)
            before = dict(memory.data)
            with self.assertRaises(ValueError):
                trainer.restore(path)
            self.assertEqual(memory.data, before)
            self.assertEqual(memory.writes, 2)
