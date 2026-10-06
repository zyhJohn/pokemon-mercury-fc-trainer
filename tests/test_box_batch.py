import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests import test_box_sort_lock
from tests.test_box_sort_lock import record


class BoxBatchTests(unittest.TestCase):
    def setup_release(self, folder, crc="4755f497"):
        return test_box_sort_lock.BoxSortLockTests().setup_release(folder, crc)

    def test_750_reference_reverse_move_is_one_backup_and_recoverable(self):
        for crc in ("b4af11c8", "4755f497"):
            with tempfile.TemporaryDirectory() as folder:
                memory, trainer, addresses = self.setup_release(folder, crc)
                original = [
                    b"".join(record(160, box * 30 + s) for s in range(30))
                    for box in range(25)
                ]
                for address, raw in zip(addresses, original):
                    memory.put(address, raw)
                references = [
                    trainer.box_reference(trainer.snapshot_box(box), slot)
                    for box in range(25)
                    for slot in range(30)
                ]
                prepared = trainer.prepare_box_batch(
                    list(reversed(references)), "move", 0
                )
                self.assertEqual(len(prepared["references"]), 750)
                result = trainer.commit_box_batch(prepared)
                actual = [
                    memory.read(a + s * 58, 58) for a in addresses for s in range(30)
                ]
                self.assertEqual(actual, [r["raw"] for r in reversed(references)])
                self.assertEqual(Counter(actual), Counter(r["raw"] for r in references))
                backup = json.loads(Path(result["backup"]).read_text("utf-8"))
                self.assertEqual(len(backup["patches"]), 25)
                trainer.restore(result["backup"])
                self.assertEqual([memory.read(a, 1740) for a in addresses], original)

    def test_reference_stale_wrong_rom_and_source_lock_refuse_without_write(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            memory.put(addresses[0], record(160, 17))
            reference = trainer.box_reference(trainer.snapshot_box(0), 0)
            trainer.locked_boxes = {0}
            with self.assertRaisesRegex(ValueError, "锁定"):
                trainer.prepare_box_batch([reference], "move", 1)
            trainer.locked_boxes = set()
            wrong = {**reference, "rom_sha256": "unknown"}
            with self.assertRaisesRegex(ValueError, "另一ROM"):
                trainer.prepare_box_batch([wrong], "move", 1)
            memory.put(addresses[0] + 18, b"\xff")
            before = dict(memory.data)
            with self.assertRaisesRegex(ValueError, "原槽已变化"):
                trainer.prepare_box_batch([reference], "move", 1)
            self.assertEqual(memory.data, before)
            self.assertEqual(memory.writes, 0)

    def test_more_than_30_targets_use_following_unlocked_boxes_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            for box in (0, 1):
                memory.put(
                    addresses[box],
                    b"".join(record(160, box * 30 + s) for s in range(30)),
                )
            refs = [
                trainer.box_reference(trainer.snapshot_box(box), slot)
                for box in (0, 1)
                for slot in range(30)
            ]
            trainer.locked_boxes = {3}
            prepared = trainer.prepare_box_batch(refs + refs[:2], "move", 2)
            self.assertEqual(len(prepared["references"]), 60)
            self.assertEqual({box for box, _ in prepared["destinations"]}, {2, 4})
            trainer.commit_box_batch(prepared)
            self.assertEqual(memory.read(addresses[0], 1740), b"\0" * 1740)
            self.assertEqual(memory.read(addresses[1], 1740), b"\0" * 1740)
            self.assertEqual(memory.read(addresses[3], 1740), b"\0" * 1740)

    def test_full_target_and_changed_lock_or_target_abort_whole_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            memory.put(addresses[0], record(160, 17))
            reference = trainer.box_reference(trainer.snapshot_box(0), 0)
            memory.put(addresses[24], b"".join(record(160, 100 + s) for s in range(30)))
            with self.assertRaisesRegex(ValueError, "空位不足"):
                trainer.prepare_box_batch([reference], "move", 24)
            prepared = trainer.prepare_box_batch([reference], "move", 1)
            trainer.locked_boxes = {1}
            with self.assertRaisesRegex(ValueError, "盒锁已变化"):
                trainer.commit_box_batch(prepared)
            trainer.locked_boxes = set()
            memory.put(addresses[1] + 18, b"\xa5")
            before = dict(memory.data)
            with self.assertRaises(OSError):
                trainer.commit_box_batch(prepared)
            self.assertEqual(memory.data, before)
            self.assertEqual(memory.writes, 0)

    def test_egg_batch_preserves_records_and_refuses_mixed_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            raw = bytearray(record(160, 17))
            raw[19] |= 4
            raw[57] |= 64
            raw[37] = 20
            original = bytes(raw)
            memory.put(addresses[0], original + record(160, 18))
            snapshot = trainer.snapshot_box(0)
            egg = trainer.box_reference(snapshot, 0)
            normal = trainer.box_reference(snapshot, 1)
            with self.assertRaisesRegex(ValueError, "全部选中成员均为已有蛋"):
                trainer.prepare_box_batch([egg, normal], "egg")
            prepared = trainer.prepare_box_batch([egg], "egg")
            result = trainer.commit_box_batch(prepared)
            actual = memory.read(addresses[0], 58)
            self.assertEqual(actual[:37], original[:37])
            self.assertEqual(actual[37], 0)
            self.assertEqual(actual[38:], original[38:])
            self.assertEqual(memory.read(addresses[0] + 58, 58), normal["raw"])
            trainer.restore(result["backup"])
            self.assertEqual(memory.read(addresses[0], 58), original)


if __name__ == "__main__":
    unittest.main()
