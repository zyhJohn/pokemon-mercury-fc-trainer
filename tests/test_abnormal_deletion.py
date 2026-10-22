"""Abnormal-only deletion, survivor identity and conditional restoration."""

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path

from pokemon_deletion import plan_deletion
from pokemon_deletion_layout import party_deletion_layout
from rom_versions import load_profile
from tests.test_box_sort_lock import record
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


def damaged(raw):
    data = bytearray(raw)
    data[19] |= 1
    return bytes(data)


class AbnormalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.use_version("b4af11c8")

    def use_version(self, crc):
        self.profile = load_profile(crc=crc)
        self.mem = Memory()
        install_profile(self.mem, self.profile)
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.mem.put(PARTY_COUNT, b"\x03")
        self.original = (
            sample().raw + damaged(sample().raw) + sample().raw + bytes([0xA5]) * 300
        )
        self.mem.put(PARTY, self.original)
        layout = party_deletion_layout(self.profile)
        self.mem.put(layout["callback_address"], layout["callbacks"])
        self.mem.put(layout["script_lock_address"], b"\0")
        for address, raw in layout["code_signatures"]:
            self.mem.put(address, raw)
        self.trainer = Trainer(self.mem, self.profile, self.temp.name)

    def test_party_each_position_and_all_versions_preserve_full_survivors_and_inactive_bytes(
        self,
    ):
        for crc in ("b4af11c8", "4755f497", "518482f7"):
            for slot in range(3):
                with self.subTest(crc=crc, slot=slot):
                    self.use_version(crc)
                    raw = bytearray(sample().raw * 3 + self.original[300:])
                    raw[slot * 100 + 19] |= 1
                    self.mem.put(PARTY, raw)
                    before = bytes(raw)
                    self.mem.put(PARTY_COUNT - 1, b"\xd1")
                    self.mem.put(PARTY_COUNT + 1, b"\xd2\xd3")
                    prepared = self.trainer.prepare_abnormal_deletion("party", slot)
                    result = self.trainer.commit_abnormal_deletion(prepared)
                    expected = (
                        before[: slot * 100]
                        + before[(slot + 1) * 100 : 300]
                        + bytes(100)
                        + before[300:]
                    )
                    self.assertEqual(self.mem.read(PARTY, 600), expected)
                    self.assertEqual(self.mem.r8(PARTY_COUNT), 2)
                    self.assertEqual(
                        self.mem.read(PARTY_COUNT - 1, 4), b"\xd1\x02\xd2\xd3"
                    )
                    self.trainer.restore(result["backup"])
                    self.assertEqual(self.mem.read(PARTY, 600), before)
                    self.assertEqual(self.mem.r8(PARTY_COUNT), 3)

    def test_box_bad_or_unknown_residual_only_target_changes_and_restores(self):
        address = self.profile["storage"]["box_addresses"][24] + 29 * 58
        for raw in (damaged(record(160, 42)), b"\x01" + bytes(57)):
            self.mem.put(address, raw)
            prepared = self.trainer.prepare_abnormal_deletion("box", 29, 24)
            self.assertEqual(prepared["context"]["before"], raw.hex())
            result = self.trainer.commit_abnormal_deletion(prepared)
            self.assertEqual(self.mem.read(address, 58), bytes(58))
            self.assertEqual(self.mem.read(PARTY, 600), self.original)
            self.trainer.restore(result["backup"])
            self.assertEqual(self.mem.read(address, 58), raw)

    def test_healthy_and_empty_targets_refused(self):
        for kind, slot, box in (("party", 0, None), ("box", 0, 0)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.trainer.prepare_abnormal_deletion(kind, slot, box)
        self.assertEqual(self.mem.writes, 0)

    def test_last_healthy_none_or_eggs_only_survivors_refused(self):
        egg = bytearray(sample().raw)
        egg[19] |= 4
        egg[75] |= 64
        for remaining in ([], [damaged(sample().raw)], [bytes(egg)]):
            records = [damaged(sample().raw), *remaining]
            raw = b"".join(records).ljust(600, b"\0")
            with (
                self.subTest(count=len(records)),
                self.assertRaisesRegex(ValueError, "健康"),
            ):
                plan_deletion(raw, self.profile, "party", 0, count=len(records))

    def test_mutated_preview_foreign_trainer_and_replay_refused(self):
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        altered = copy.deepcopy(prepared)
        altered["context"]["slot"] = 0
        other = Trainer(self.mem, self.profile, self.temp.name)
        for trainer, candidate in ((self.trainer, altered), (other, prepared)):
            with self.assertRaisesRegex(ValueError, "失效"):
                trainer.commit_abnormal_deletion(candidate)
        self.trainer.commit_abnormal_deletion(prepared)
        with self.assertRaisesRegex(ValueError, "失效"):
            self.trainer.commit_abnormal_deletion(prepared)

    def test_transport_reconnection_invalidates_preview_even_if_trainer_is_reused(self):
        self.mem.connection_generation = "before"
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        self.mem.connection_generation = "after"
        with self.assertRaisesRegex(ValueError, "失效"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.assertEqual(self.mem.writes, 0)

    def test_party_field_and_script_lock_required_even_when_main_callback_is_unchanged(
        self,
    ):
        layout = party_deletion_layout(self.profile)
        for address, raw in (
            (layout["callback_address"], bytes(8)),
            (layout["script_lock_address"], b"\1"),
        ):
            before = self.mem.read(address, len(raw))
            self.mem.put(address, raw)
            with self.assertRaisesRegex(ValueError, "普通地图"):
                self.trainer.prepare_abnormal_deletion("party", 1)
            self.mem.put(address, before)
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        self.mem.put(layout["script_lock_address"], b"\1")
        with self.assertRaisesRegex(ValueError, "普通地图"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.assertEqual(self.mem.writes, 0)

    def test_stale_survivor_and_inactive_bytes_refuse_without_writes(self):
        for offset in (0, 205, 599):
            self.use_version("b4af11c8")
            prepared = self.trainer.prepare_abnormal_deletion("party", 1)
            self.mem.put(PARTY + offset, b"\x99")
            before = dict(self.mem.data)
            with self.assertRaises(OSError):
                self.trainer.commit_abnormal_deletion(prepared)
            self.assertEqual(self.mem.data, before)
            self.assertEqual(self.mem.writes, 0)

    def test_box_lock_battle_and_old_bridge_refuse(self):
        address = self.profile["storage"]["box_addresses"][0]
        self.mem.put(address, damaged(record(160, 1)))
        prepared = self.trainer.prepare_abnormal_deletion("box", 0, 0)
        self.trainer.locked_boxes.add(0)
        with self.assertRaisesRegex(ValueError, "锁"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.trainer.locked_boxes.clear()
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        flag = self.profile["battle_flag"]
        self.mem.put(flag["address"], bytes([flag["mask"]]))
        with self.assertRaisesRegex(ValueError, "战斗"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.mem.put(flag["address"], bytes(1))
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        self.mem.capabilities = self.mem.capabilities - {"BATCHVERIFY"}
        with self.assertRaisesRegex(ValueError, "读回"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.assertEqual(self.mem.writes, 0)

    def test_ordinary_edits_or_move_cannot_sanitize_bad_record_and_healthy_other_can_edit(
        self,
    ):
        snap = self.trainer.snapshot()
        with self.assertRaises(ValueError):
            self.trainer.edit_pokemon(snap, 1, ivs=[31] * 6)
        with self.assertRaises(ValueError):
            self.trainer.commit(
                snap, [(PARTY + 100, damaged(sample().raw), sample().raw)], "bypass"
            )
        patches, _ = self.trainer.edit_pokemon(snap, 0, ivs=[31] * 6)
        self.trainer.commit(snap, patches, "healthy")
        self.assertEqual(self.mem.read(PARTY + 100, 100), damaged(sample().raw))

    def test_partial_and_cross_record_mutations_cannot_bypass_diagnostics(self):
        address = self.profile["storage"]["box_addresses"][0]
        self.mem.put(address, damaged(record(160, 1)))
        for a, size in (
            (PARTY + 119, 1),
            (PARTY + 99, 2),
            (PARTY, 600),
            (address + 19, 1),
            (address + 57, 2),
            (address - 1, 2),
        ):
            old = self.mem.read(a, size)
            new = bytearray(old)
            new[-1] ^= 1
            with (
                self.subTest(address=a, size=size),
                self.assertRaisesRegex(ValueError, "完整记录"),
            ):
                self.trainer.commit(
                    self.trainer.snapshot(), [(a, old, bytes(new))], "partial"
                )
        self.assertEqual(self.mem.writes, 0)

    def test_backup_tampering_stale_restore_and_uncertain_write_refuse(self):
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)
        result = self.trainer.commit_abnormal_deletion(prepared)
        path = Path(result["backup"])
        backup = json.loads(path.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(backup)
        tampered["patches"][0]["after"] = "01"
        corrupt_path = Path(self.temp.name) / "tampered.json"
        corrupt_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.trainer.restore(corrupt_path)
        self.mem.put(PARTY + 599, b"\x00")
        before = dict(self.mem.data)
        with self.assertRaises(OSError):
            self.trainer.restore(path)
        self.assertEqual(self.mem.data, before)
        self.use_version("b4af11c8")
        prepared = self.trainer.prepare_abnormal_deletion("party", 1)

        def fail(patches, **kwargs):
            self.mem.put(PARTY_COUNT, b"\x02")
            raise OSError("interrupted")

        self.mem.batch = fail
        with self.assertRaises(OSError):
            self.trainer.commit_abnormal_deletion(prepared)
        failed = [
            p
            for p in Path(self.temp.name).glob("*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("status")
            == "failed-or-unconfirmed"
        ]
        self.assertTrue(failed)
        with self.assertRaisesRegex(ValueError, "未被确认"):
            self.trainer.restore(failed[-1])
