"""Verified repel and rival transactions across both supported releases."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from name_codec import encode_name
from player_rival import RIVAL_OFFSET, RIVAL_POINTER
from rom_versions import load_profile
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from tests import test_end_to_end
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


REPEL = 0x0202656C
SOURCE = 0x030053C0
SAVE1 = 0x0202552C
SAVE2 = 0x02024588


class FieldTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fixture(self, crc):
        profile = load_profile(crc=crc)
        mem = Memory()
        install_profile(mem, profile)
        mem.put(SAVE_POINTER, struct.pack("<I", SAVE1))
        mem.put(RIVAL_POINTER, struct.pack("<I", SAVE1))
        mem.put(profile["trainer"]["pointer_address"], struct.pack("<I", SAVE2))
        mem.put(SOURCE, struct.pack("<I", REPEL - 0x50))
        mem.put(REPEL, struct.pack("<H", 200))
        mem.put(SAVE1 + RIVAL_OFFSET, encode_name("劲敌A", 8))
        mem.put(SAVE2, encode_name("玩家A", 8) + b"\x00\x00" + struct.pack("<HH", 123, 456))
        mem.put(PARTY_COUNT, b"\x01")
        mem.put(PARTY, sample().raw)
        return Trainer(mem, profile, self.temp.name), mem, profile

    def test_both_releases_write_read_restore_and_single_combined_backup(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                trainer, mem, profile = self.fixture(crc)
                repel = trainer.prepare_repel_steps(250)
                result = trainer.commit_repel_steps(repel)
                self.assertEqual(trainer.snapshot_repel()["steps"], 250)
                trainer.restore(result["backup"])
                self.assertEqual(trainer.snapshot_repel()["steps"], 200)
                prepared = trainer.prepare_player_rival(65535, 0, "大力鳄A", "小智A")
                result = trainer.commit_player_rival(prepared)
                record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
                self.assertEqual(len(record["patches"]), 3)
                self.assertEqual(trainer.snapshot_trainer()["name"], "大力鳄A")
                self.assertEqual(trainer.snapshot_rival()["name"], "小智A")
                self.assertEqual(mem.read(PARTY, 100), sample().raw)
                trainer.restore(result["backup"])
                self.assertEqual(trainer.snapshot_trainer()["name"], "玩家A")
                self.assertEqual(trainer.snapshot_rival()["name"], "劲敌A")

    def test_stale_connection_old_value_and_source_pointer_write_nothing(self):
        trainer, mem, profile = self.fixture("b4af11c8")
        prepared = trainer.prepare_repel_steps(10)
        other = Trainer(mem, profile, self.temp.name)
        with self.assertRaisesRegex(ValueError, "旧连接"):
            other.commit_repel_steps(prepared)
        mem.put(REPEL, b"\x01\x00")
        with self.assertRaisesRegex(ValueError, "已变化"):
            trainer.commit_repel_steps(prepared)
        self.assertEqual(mem.writes, 0)
        mem.put(REPEL, b"\xc8\x00")
        prepared = trainer.prepare_repel_steps(10)
        mem.put(SOURCE, struct.pack("<I", REPEL - 0x4E))
        with self.assertRaises(ValueError):
            trainer.commit_repel_steps(prepared)
        self.assertEqual(mem.writes, 0)

    def test_repel_relocation_changes_address_and_old_preview_fails(self):
        trainer, mem, _ = self.fixture("4755f497")
        old = trainer.prepare_repel_steps(0)
        new_save1 = SAVE1 + 0x1000
        mem.put(SAVE_POINTER, struct.pack("<I", new_save1))
        mem.put(SOURCE, struct.pack("<I", new_save1 + 0xFF0))
        mem.put(new_save1 + 0x1040, b"\x64\x00")
        with self.assertRaises(ValueError):
            trainer.commit_repel_steps(old)
        self.assertEqual(mem.writes, 0)
        relocated = trainer.prepare_repel_steps(250)
        self.assertEqual(relocated["address"], new_save1 + 0x1040)
        result = trainer.commit_repel_steps(relocated)
        self.assertEqual(trainer.snapshot_repel()["steps"], 250)
        trainer.restore(result["backup"])
        self.assertEqual(trainer.snapshot_repel()["steps"], 100)

    def test_rival_pointer_old_value_and_bad_name_refuse_write(self):
        trainer, mem, _ = self.fixture("4755f497")
        prepared = trainer.prepare_rival_name("劲敌B")
        mem.put(RIVAL_POINTER, struct.pack("<I", SAVE1 + 4))
        with self.assertRaises(ValueError):
            trainer.commit_rival_name(prepared)
        self.assertEqual(mem.writes, 0)
        mem.put(RIVAL_POINTER, struct.pack("<I", SAVE1))
        mem.put(SAVE1 + RIVAL_OFFSET, b"\x06\x02\xff" + b"\xff" * 5)
        self.assertIsNone(trainer.snapshot_rival()["name"])
        with self.assertRaises(ValueError):
            trainer.prepare_rival_name("小智")
        mem.put(SAVE1 + RIVAL_OFFSET, b"\xbb\xff" + b"\x00" * 6)
        self.assertEqual(trainer.snapshot_rival()["name"], "A")
        mem.put(SAVE1 + RIVAL_OFFSET, b"\x06\x02\xff" + b"\xff" * 5)
        keep = trainer.prepare_player_rival(4, 5, None, None)
        result = trainer.commit_player_rival(keep)
        self.assertEqual(mem.read(SAVE1 + RIVAL_OFFSET, 8), b"\x06\x02\xff" + b"\xff" * 5)
        trainer.restore(result["backup"])
        self.assertEqual(mem.writes, 2)

    def test_battle_ranges_and_bad_backup_have_zero_writes(self):
        trainer, mem, profile = self.fixture("b4af11c8")
        for value in (-1, 251, True):
            with self.assertRaises(ValueError):
                trainer.prepare_repel_steps(value)
        for name in ("ABCDEFGH", "😀", ""):
            with self.assertRaises(ValueError):
                trainer.prepare_rival_name(name)
        flag = profile["battle_flag"]
        mem.put(flag["address"], bytes([flag["mask"]]))
        prepared = trainer.prepare_rival_name("小智")
        with self.assertRaisesRegex(ValueError, "战斗"):
            trainer.commit_rival_name(prepared)
        self.assertEqual(mem.writes, 0)
        mem.put(flag["address"], b"\0")
        result = trainer.commit_rival_name(trainer.prepare_rival_name("小智"))
        record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        record["patches"][0]["address"] = REPEL + 2
        bad = Path(self.temp.name, "tampered.json")
        bad.write_text(json.dumps(record), encoding="utf-8")
        count = mem.writes
        with self.assertRaises(ValueError):
            trainer.restore(bad)
        self.assertEqual(mem.writes, count)
        trainer.restore(result["backup"])


@unittest.skipIf(test_end_to_end.LuaRuntime is None, "lupa is required")
class FieldLuaTcpTests(unittest.TestCase):
    def setUp(self):
        test_end_to_end.EndToEndTests.setUp(self)

    def test_both_releases_callback_backup_readback_and_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                profile = load_profile(crc=crc)
                install_profile(self.memory, profile)
                self.memory.put(SOURCE, struct.pack("<I", REPEL - 0x50))
                self.memory.put(REPEL, struct.pack("<H", 100))
                self.memory.put(RIVAL_POINTER, struct.pack("<I", SAVE1))
                self.memory.put(SAVE1 + RIVAL_OFFSET, encode_name("劲敌A", 8))
                self.memory.put(profile["trainer"]["pointer_address"], struct.pack("<I", SAVE2))
                self.memory.put(SAVE2, encode_name("玩家A", 8) + b"\0\0"
                                + struct.pack("<HH", 123, 456))
                trainer = Trainer(self.client, profile, self.temp.name)
                sent = []
                self.after_command = (lambda line: sent.append(line)
                                      if line.startswith(b"BATCHVERIFYCRC") else None)
                prepared = trainer.prepare_repel_steps(0)
                record = trainer.commit_repel_steps(prepared)
                self.assertEqual(trainer.snapshot_repel()["steps"], 0)
                trainer.restore(record["backup"])
                self.assertEqual(trainer.snapshot_repel()["steps"], 100)
                prepared = trainer.prepare_player_rival(44, 55, "大力鳄A", "小智A")
                record = trainer.commit_player_rival(prepared)
                self.assertEqual((trainer.snapshot_trainer()["tid"],
                                  trainer.snapshot_rival()["name"]), (44, "小智A"))
                trainer.restore(record["backup"])
                self.assertEqual(trainer.snapshot_rival()["name"], "劲敌A")
                self.assertEqual(trainer.snapshot_trainer()["tid"], 123)
                self.assertEqual(len(sent), 4)
                prepared = trainer.prepare_rival_name("小智")
                self.memory.put(RIVAL_POINTER, struct.pack("<I", SAVE1 + 4))
                writes = self.memory.writes
                with self.assertRaises(ValueError):
                    trainer.commit_rival_name(prepared)
                self.assertEqual(self.memory.writes, writes)
                self.memory.put(RIVAL_POINTER, struct.pack("<I", SAVE1))

    def test_repel_frame_change_after_callback_keeps_verified_backup(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                profile = load_profile(crc=crc)
                install_profile(self.memory, profile)
                self.memory.put(SOURCE, struct.pack("<I", REPEL - 0x50))
                self.memory.put(REPEL, b"\x64\x00")
                trainer = Trainer(self.client, profile, self.temp.name)
                calls = []

                def advance_after_command(line):
                    if line.startswith(b"BATCHVERIFYCRC"):
                        calls.append(line)
                        self.memory.put(REPEL, b"\xf9\x00")

                self.after_command = advance_after_command
                result = trainer.commit_repel_steps(trainer.prepare_repel_steps(250))
                self.assertEqual(len(calls), 1)
                record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "verified")
                self.assertEqual(self.memory.read(REPEL, 2), b"\xf9\x00")


if __name__ == "__main__":
    unittest.main()
