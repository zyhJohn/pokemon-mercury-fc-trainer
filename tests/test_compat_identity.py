"""Actual ROM identity is independent from the verified address layout."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clock_data import digest, read_save, restore_calendar, write_calendar
from game_fields import box_name_address
from tests import test_box_names_transactions
from tests.test_box_sort_lock import record
from tests.test_time import make_save


class CompatibleTransactionIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.mem, self.trainer = test_box_names_transactions.BoxNameTransactionTests().setup_name(
            temp.name, "4755f497"
        )
        self.known = dict(self.trainer.profile)
        self.activate("11111111")
        # These tests isolate transaction identity from the structural matcher.
        self.runtime = patch("trainer_core.runtime_compatibility_check")
        self.check = self.runtime.start()
        self.addCleanup(self.runtime.stop)

    def activate(self, crc):
        self.trainer.profile = {
            **self.known,
            "compatibility": {"test": "transaction identity fixture"},
            "rom_crc32": crc,
            "rom_identity": "crc32:" + crc,
        }
        self.mem.rom_crc32 = crc

    def test_actual_crc_signatures_and_runtime_check_all_gate_verify(self):
        self.trainer.verify()
        self.check.assert_called_once_with(self.mem, self.trainer.profile, full=False)
        self.assertEqual(self.mem.expected_rom_crc32, "11111111")
        self.mem.rom_crc32 = "22222222"
        with self.assertRaisesRegex(ValueError, "CRC32"):
            self.trainer.verify()
        self.assertFalse(self.trainer.verified)
        self.mem.rom_crc32 = "11111111"
        signature = self.trainer.profile["signatures"][0]
        self.mem.put(signature["address"], b"\0" * len(bytes.fromhex(signature["hex"])))
        with self.assertRaisesRegex(ValueError, "布局"):
            self.trainer.verify()
        self.assertFalse(self.trainer.verified)

    def test_runtime_rejection_prevents_transaction_and_backup(self):
        prepared = self.trainer.prepare_box_name(0, "新盒")
        def runtime_check(memory, profile, *, full=True):
            if full:
                raise ValueError("兼容指纹已变化")

        self.check.side_effect = runtime_check
        with self.assertRaisesRegex(ValueError, "兼容指纹"):
            self.trainer.commit_box_name(prepared)
        self.assertTrue(self.check.call_args.kwargs["full"])
        self.assertEqual(self.mem.writes, 0)
        self.assertEqual(list(self.folder.glob("*.json")), [])

    def test_same_layout_backup_is_bound_to_actual_rom(self):
        prepared = self.trainer.prepare_box_name(0, "新盒")
        self.assertEqual(prepared["address"], box_name_address(self.known["rom_sha256"], 0))
        result = self.trainer.commit_box_name(prepared)
        backup_path = Path(result["backup"])
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(backup["schema"], 1)
        self.assertEqual(backup["rom_sha256"], self.known["rom_sha256"])
        self.assertEqual(backup["rom_identity"], "crc32:11111111")
        writes = self.mem.writes
        self.activate("22222222")
        with self.assertRaisesRegex(ValueError, "ROM"):
            self.trainer.restore(backup_path)
        self.assertEqual(self.mem.writes, writes)
        self.activate("11111111")
        self.trainer.restore(backup_path)
        self.assertEqual(self.mem.read(prepared["address"], 9), prepared["raw"])

    def test_previews_references_and_snapshots_reject_another_actual_rom(self):
        base = self.trainer.profile["storage"]["box_addresses"][0]
        self.mem.put(base, record(160, 17))
        box = self.trainer.snapshot_box(0)
        target = self.trainer.snapshot_box(1)
        reference = self.trainer.box_reference(box, 0)
        batch = self.trainer.prepare_box_batch([reference], "move", 1)
        sort = self.trainer.prepare_box_sort()
        rename = self.trainer.prepare_box_name(0, "新盒")
        snap = self.trainer.snapshot()
        patches = self.trainer.edit_bag(snap, 0, 13, 2)
        self.activate("22222222")
        for operation in (
            lambda: self.trainer.prepare_box_batch([reference], "move", 1),
            lambda: self.trainer.commit_box_batch(batch),
            lambda: self.trainer.commit_box_sort(sort),
            lambda: self.trainer.commit_box_name(rename),
            lambda: self.trainer.box_reference(box, 0),
            lambda: self.trainer.edit_box(box, 0, shiny=True),
            lambda: self.trainer.prepare_box_move(box, 0, target),
            lambda: self.trainer.commit_box_move(box, 0, target),
            lambda: self.trainer.commit(snap, patches, "stale ROM snapshot"),
        ):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "ROM"):
                operation()
        self.assertEqual(self.mem.writes, 0)

    def test_gift_and_creation_previews_bind_actual_identity(self):
        draft = dict(
            species=25, level=5, pid=0x12345678, otid=0x87654321,
            nickname="皮卡丘", ot_name="小智", moves=(33, 0, 0, 0),
        )
        previews = (
            (self.trainer.prepare_gift_box("mercury-home-165", 0), self.trainer.commit_gift_box),
            (self.trainer.prepare_gift_party("mercury-home-165"), self.trainer.commit_gift_party),
            (self.trainer.prepare_create_box(0, draft), self.trainer.commit_create_box),
            (self.trainer.prepare_create_party(draft), self.trainer.commit_create_party),
        )
        self.activate("22222222")
        for prepared, commit in previews:
            self.assertEqual(prepared["rom_identity"], "crc32:11111111")
            with self.subTest(commit=commit), self.assertRaisesRegex(ValueError, "ROM"):
                commit(prepared)
        self.assertEqual(self.mem.writes, 0)

    def test_legacy_backup_restores_known_release_but_refuses_compatibility(self):
        self.trainer.profile = self.known
        self.mem.rom_crc32 = self.known["rom_crc32"]
        result = self.trainer.commit_box_name(self.trainer.prepare_box_name(0, "新盒"))
        path = Path(result["backup"])
        backup = json.loads(path.read_text(encoding="utf-8"))
        del backup["rom_identity"]
        path.write_text(json.dumps(backup), encoding="utf-8")
        self.activate("11111111")
        with self.assertRaisesRegex(ValueError, "ROM"):
            self.trainer.restore(path)
        self.trainer.profile = self.known
        self.mem.rom_crc32 = self.known["rom_crc32"]
        self.trainer.restore(path)
        self.assertEqual(self.mem.writes, 2)


class CompatibleRtcIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.save = self.folder / "game.sav"
        self.save.write_bytes(make_save())
        self.rom_a = self.folder / "a.gba"
        self.rom_b = self.folder / "b.gba"
        self.rom_a.write_bytes(b"modified ROM A")
        self.rom_b.write_bytes(b"modified ROM B")
        self.a, self.b = (self.profile(path) for path in (self.rom_a, self.rom_b))
        clock = patch("clock_data.time.time", return_value=1791021600)
        clock.start()
        self.addCleanup(clock.stop)

    def profile(self, path):
        actual = digest(path.read_bytes())
        return {
            "rom_sha256": "baseline layout hash",
            "actual_rom_sha256": actual,
            "rom_identity": "sha256:" + actual,
            "compatibility": {"test": "RTC identity fixture"},
        }

    def test_modified_local_hash_and_rtc_backup_restore_identity(self):
        snap = read_save(self.save, self.rom_a, self.a)
        result = write_calendar(snap, "2024-02-29 23:59:59", self.rom_a, self.a, self.folder)
        path = Path(result["backup"])
        record_data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record_data["rom_identity"], self.a["rom_identity"])
        self.assertEqual(record_data["rom_sha256"], self.a["rom_sha256"])
        current = self.save.read_bytes()
        other = read_save(self.save, self.rom_b, self.b)
        with self.assertRaisesRegex(ValueError, "ROM"):
            restore_calendar(other, path, self.rom_b, self.b, self.folder)
        self.assertEqual(self.save.read_bytes(), current)
        own = read_save(self.save, self.rom_a, self.a)
        restore_calendar(own, path, self.rom_a, self.a, self.folder)
        self.assertEqual(self.save.read_bytes(), snap["data"])

    def test_local_rom_hash_and_stale_rtc_snapshot_refuse_cross_rom_write(self):
        snap = read_save(self.save, self.rom_a, self.a)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            read_save(self.save, self.rom_b, self.a)
        with self.assertRaisesRegex(ValueError, "另一 ROM"):
            write_calendar(snap, "2024-02-29 23:59:59", self.rom_b, self.b, self.folder)
        online_only = {**self.a}
        del online_only["actual_rom_sha256"]
        with self.assertRaisesRegex(ValueError, "本地 ROM"):
            read_save(self.save, self.rom_a, online_only)
        self.assertEqual(self.save.read_bytes(), snap["data"])

    def test_legacy_rtc_backup_known_release_restores_and_compatibility_refuses(self):
        known = {"rom_sha256": digest(self.rom_a.read_bytes())}
        original = read_save(self.save, self.rom_a, known)
        result = write_calendar(original, "2024-02-29 23:59:59", self.rom_a, known, self.folder)
        path = Path(result["backup"])
        backup = json.loads(path.read_text(encoding="utf-8"))
        del backup["rom_identity"]
        path.write_text(json.dumps(backup), encoding="utf-8")
        compatible = {**self.a, "rom_sha256": known["rom_sha256"]}
        modified_snapshot = read_save(self.save, self.rom_a, compatible)
        with self.assertRaisesRegex(ValueError, "ROM"):
            restore_calendar(modified_snapshot, path, self.rom_a, compatible, self.folder)
        snapshot = read_save(self.save, self.rom_a, known)
        restore_calendar(snapshot, path, self.rom_a, known, self.folder)
        self.assertEqual(self.save.read_bytes(), original["data"])


if __name__ == "__main__":
    unittest.main()
