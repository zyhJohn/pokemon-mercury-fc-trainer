"""Official native PC gifts and party-count transaction boundary over Lua/TCP."""

import json
import unittest
from pathlib import Path

from distribution_catalog import load_distributions
from rom_versions import load_profile
from tests import test_end_to_end
from tests.test_bridge import LuaRuntime
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_core import PARTY, PARTY_COUNT, Trainer


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class GiftTransactionTests(unittest.TestCase):
    def setUp(self):
        test_end_to_end.EndToEndTests.setUp(self)
        self.sent = []
        self.after_command = lambda line: (
            self.sent.append(line) if line.startswith(b"BATCHCRC ") else None
        )

    def use_version(self, crc):
        profile = load_profile(crc=crc)
        install_profile(self.memory, profile)
        self.trainer = Trainer(self.client, profile, self.temp.name)
        return profile["storage"]["box_addresses"]

    def test_official_gift_each_version_one_backup_and_conditional_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                addresses = self.use_version(crc)
                gift_id = "mercury-home-165"
                prepared = self.trainer.prepare_gift_box(gift_id, 24)
                self.assertEqual(prepared["destination"], (24, 0))
                expected = bytes.fromhex(
                    next(r for r in load_distributions()["rows"] if r["id"] == gift_id)
                    ["template"]["native_pc_hex"]
                )
                self.assertEqual(prepared["record"], expected)
                result = self.trainer.commit_gift_box(prepared)
                self.assertEqual(len(self.sent), 1)
                self.assertEqual(self.client.read(addresses[24], 58), expected)
                backup = json.loads(Path(result["backup"]).read_text("utf-8"))
                self.assertEqual(backup["status"], "verified")
                self.assertEqual(len(backup["patches"]), 1)
                self.memory.put(addresses[24] + 18, bytes([expected[18] ^ 1]))
                with self.assertRaises(OSError):
                    self.trainer.restore(result["backup"])
                self.memory.put(addresses[24] + 18, expected[18:19])
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(addresses[24], 58), b"\0" * 58)
                self.sent.clear()

    def test_all_six_curated_templates_prepare_on_both_rom_versions(self):
        rows = [r for r in load_distributions()["rows"] if r["compatibility"] == "verified"]
        self.assertEqual(len(rows), 6)
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                self.use_version(crc)
                for row in rows:
                    prepared = self.trainer.prepare_gift_box(row["id"], 0)
                    self.assertEqual(prepared["template"]["id"], row["id"])
                    self.assertEqual(len(prepared["record"]), 58)

    def test_gift_rejects_occupied_locked_stale_or_forged_preview(self):
        addresses = self.use_version("4755f497")
        prepared = self.trainer.prepare_gift_box("mercury-home-496", 0, slot=4)
        reconnected = Trainer(self.client, self.trainer.profile, self.temp.name)
        with self.assertRaisesRegex(ValueError, "旧连接"):
            reconnected.commit_gift_box(prepared)
        self.trainer.locked_boxes = {0}
        with self.assertRaisesRegex(ValueError, "锁定"):
            self.trainer.commit_gift_box(prepared)
        self.trainer.locked_boxes.clear()
        forged = {**prepared, "record": b"\x01" * 58}
        with self.assertRaisesRegex(ValueError, "记录已变化"):
            self.trainer.commit_gift_box(forged)
        self.memory.put(addresses[0] + 4 * 58, b"\x01")
        with self.assertRaises(OSError):
            self.trainer.commit_gift_box(prepared)
        self.assertEqual(self.memory.writes, 0)
        with self.assertRaisesRegex(ValueError, "全零"):
            self.trainer.prepare_gift_box("mercury-home-496", 0, slot=4)
        with self.assertRaisesRegex(ValueError, "不能修改"):
            self.trainer.prepare_gift_box("mercury-home-496", 0, draft={"pid": 42})

    def test_verified_creator_inserts_pc_without_accepting_raw_on_both_versions(self):
        draft = {
            "species": 160,
            "level": 5,
            "pid": 123,
            "otid": 54321,
            "nickname": "TEST",
            "ot_name": "TEST",
            "moves": [33, 0, 0, 0],
        }
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                addresses = self.use_version(crc)
                with self.assertRaisesRegex(ValueError, "原始记录"):
                    self.trainer.prepare_create_box(0, {"raw": b"\x01" * 58})
                prepared = self.trainer.prepare_create_box(0, draft, slot=29)
                result = self.trainer.commit_create_box(prepared)
                self.assertEqual(
                    self.client.read(addresses[0] + 29 * 58, 58), prepared["record"]
                )
                self.assertEqual(prepared["pokemon"].describe(self.trainer.profile)["errors"], [])
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(addresses[0] + 29 * 58, 58), b"\0" * 58)
                self.sent.clear()

    def test_party_count_requires_full_zero_edge_member_same_callback_and_restores(self):
        self.use_version("4755f497")
        self.memory.put(PARTY_COUNT, b"\0")
        self.memory.put(PARTY, b"\0" * 100)
        snapshot = self.trainer.snapshot()
        patches = [
            (PARTY, b"\0" * 100, sample().raw),
            (PARTY_COUNT, b"\0", b"\1"),
        ]
        with self.assertRaisesRegex(ValueError, "对应完整成员"):
            self.trainer.commit(snapshot, patches[1:], "invalid count")
        result = self.trainer.commit(snapshot, patches, "count transaction fixture")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.client.read(PARTY_COUNT, 1), b"\1")
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(PARTY_COUNT, 1), b"\0")
        self.assertEqual(self.client.read(PARTY, 100), b"\0" * 100)

    def test_party_gift_append_and_explicit_full_replacement_both_versions(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                self.use_version(crc)
                self.memory.put(PARTY_COUNT, b"\0")
                self.memory.put(PARTY, b"\0" * 600)
                prepared = self.trainer.prepare_gift_party("mercury-home-165")
                self.assertEqual(prepared["destination"], ("party", 0))
                self.assertIsNone(prepared["replaced"])
                result = self.trainer.commit_gift_party(prepared)
                self.assertEqual(self.client.read(PARTY, 100), prepared["record"])
                self.assertEqual(self.client.read(PARTY_COUNT, 1), b"\1")
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(PARTY_COUNT, 1), b"\0")
                self.assertEqual(self.client.read(PARTY, 100), b"\0" * 100)

                self.memory.put(PARTY_COUNT, b"\6")
                self.memory.put(PARTY, sample().raw * 6)
                with self.assertRaisesRegex(ValueError, "必须明确"):
                    self.trainer.prepare_gift_party("mercury-home-165")
                prepared = self.trainer.prepare_gift_party("mercury-home-165", replacement_slot=2)
                self.assertEqual(prepared["destination"], ("party", 2))
                self.assertEqual(prepared["replaced"].raw, sample().raw)
                result = self.trainer.commit_gift_party(prepared)
                self.assertEqual(self.client.read(PARTY + 200, 100), prepared["record"])
                self.assertEqual(self.client.read(PARTY_COUNT, 1), b"\6")
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(PARTY + 200, 100), sample().raw)
                self.sent.clear()

    def test_backup_cannot_authorize_out_of_count_slot_with_noop_or_extra_count(self):
        self.use_version("4755f497")
        self.memory.put(PARTY_COUNT, b"\1")
        self.memory.put(PARTY, sample().raw + b"\0" * 500)
        prepared = self.trainer.prepare_gift_box("mercury-home-165", 0)
        result = self.trainer.commit_gift_box(prepared)
        path = Path(result["backup"])
        original = json.loads(path.read_text("utf-8"))
        tail_patch = {
            "address": PARTY + 500,
            "before": sample().raw.hex(),
            "after": (b"\0" * 100).hex(),
        }
        record = {**original, "patches": [
            {"address": PARTY_COUNT, "before": "01", "after": "01"},
            tail_patch,
        ]}
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "数量与末位成员"):
            self.trainer.restore(path)
        record["patches"][0] = {
            "address": PARTY_COUNT, "before": "00", "after": "01"
        }
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "数量与末位成员"):
            self.trainer.restore(path)
        self.assertEqual(self.client.read(PARTY + 500, 100), b"\0" * 100)


if __name__ == "__main__":
    unittest.main()
