import json
import tempfile
import unittest
from pathlib import Path

from game_fields import box_name_address, decode_box_name, encode_box_name
from tests import test_box_sort_lock
from trainer_core import Trainer


class BoxNameTransactionTests(unittest.TestCase):
    def setup_name(self, folder, crc):
        memory, trainer, _ = test_box_sort_lock.BoxSortLockTests().setup_release(folder, crc)
        sha = trainer.profile["rom_sha256"]
        table = {
            "b4af11c8": 0x09DD7210,
            "4755f497": 0x09DDEBCC,
        }[crc]
        memory.put(0x03005418, (0x02031294).to_bytes(4, "little"))
        for index in range(25):
            memory.put(table + 4 * index, box_name_address(sha, index).to_bytes(4, "little"))
            memory.put(box_name_address(sha, index), encode_box_name(f"盒{index + 1}"))
        return memory, trainer

    def test_both_versions_rename_backup_restore_and_guards(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc), tempfile.TemporaryDirectory() as folder:
                memory, trainer = self.setup_name(folder, crc)
                prepared = trainer.prepare_box_name(24, "新盒A")
                self.assertEqual(prepared["name"], "盒25")
                result = trainer.commit_box_name(prepared)
                address = prepared["address"]
                self.assertEqual(decode_box_name(memory.read(address, 9)), "新盒A")
                backup = json.loads(Path(result["backup"]).read_text("utf-8"))
                self.assertEqual(backup["status"], "verified")
                self.assertEqual(len(backup["patches"]), 1)
                self.assertEqual(backup["patches"][0]["address"], address)
                trainer.restore(result["backup"])
                self.assertEqual(decode_box_name(memory.read(address, 9)), "盒25")

    def test_lock_stale_name_pointer_and_old_connection_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer = self.setup_name(folder, "4755f497")
            trainer.locked_boxes = {0}
            with self.assertRaisesRegex(ValueError, "锁定"):
                trainer.prepare_box_name(0, "新盒")
            trainer.locked_boxes.clear()
            prepared = trainer.prepare_box_name(0, "新盒")
            trainer.locked_boxes = {0}
            with self.assertRaisesRegex(ValueError, "锁定"):
                trainer.commit_box_name(prepared)
            trainer.locked_boxes.clear()
            new_trainer = Trainer(memory, trainer.profile, folder)
            with self.assertRaisesRegex(ValueError, "旧连接"):
                new_trainer.commit_box_name(prepared)
            memory.put(prepared["address"], encode_box_name("已改变"))
            with self.assertRaises(OSError):
                trainer.commit_box_name(prepared)
            self.assertEqual(memory.writes, 0)
            self.assertEqual(decode_box_name(memory.read(prepared["address"], 9)), "已改变")
            memory.put(0x03005418, (0x02031298).to_bytes(4, "little"))
            with self.assertRaisesRegex(ValueError, "指针"):
                trainer.snapshot_box_name(0)


if __name__ == "__main__":
    unittest.main()
