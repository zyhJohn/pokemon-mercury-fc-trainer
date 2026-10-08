"""Daycare pending flag transactions and conservative conditional recovery."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from rom_versions import load_profile
from tests import test_end_to_end
from tests.test_core import Memory
from tests.test_daycare_data import parent
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


SAVE1 = 0x0202552C
DAYCARE = SAVE1 + 0x2F80
FLAG = SAVE1 + 0xF2C
PARENTS = parent() + parent(132)


def install_daycare(memory, save1=SAVE1, *, flag=0x80, token=0):
    memory.put(SAVE_POINTER, struct.pack("<I", save1))
    raw = bytearray(PARENTS + b"\0\0\0")
    struct.pack_into("<H", raw, 0x118, token)
    memory.put(save1 + 0x2F80, raw)
    memory.put(save1 + 0xF2C, bytes((flag,)))


class DaycareTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fixture(self, crc):
        profile = load_profile(crc=crc)
        memory = Memory()
        install_profile(memory, profile)
        install_daycare(memory)
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        return Trainer(memory, profile, self.temp.name), memory, profile

    def test_both_releases_set_only_pending_bit_and_restore_same_connection(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                trainer, mem, _ = self.fixture(crc)
                view = trainer.snapshot_daycare()
                self.assertTrue(view["eligible"])
                self.assertEqual(view["compatibility_score"], 20)
                self.assertEqual(view["parents"][0].nickname, "TEST")
                prepared = trainer.prepare_daycare_egg()
                self.assertEqual((prepared["address"], prepared["before"],
                                  prepared["after"]), (FLAG, b"\x80", b"\xc0"))
                before = mem.read(DAYCARE, 0x11B)
                result = trainer.commit_daycare_egg(prepared)
                self.assertEqual(mem.read(FLAG, 1), b"\xc0")
                self.assertEqual(mem.read(DAYCARE, 0x11B), before)
                record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "verified")
                self.assertEqual(record["daycare_context"]["daycare_raw"], before.hex())
                trainer.restore(result["backup"])
                self.assertEqual(mem.read(FLAG, 1), b"\x80")
                self.assertEqual(mem.read(DAYCARE, 0x11B), before)
                with self.assertRaises(ValueError):
                    trainer.restore(result["backup"])

    def test_ineligible_battle_stale_parent_flag_pointer_and_token_zero_writes(self):
        trainer, mem, profile = self.fixture("b4af11c8")
        flag = profile["battle_flag"]
        mem.put(flag["address"], bytes((flag["mask"],)))
        with self.assertRaisesRegex(ValueError, "战斗"):
            trainer.prepare_daycare_egg()
        mem.put(flag["address"], b"\0")
        for address, raw in ((DAYCARE + 0x118, b"\x01\x00"),
                             (FLAG, b"\xc0")):
            original = mem.read(address, len(raw))
            mem.put(address, raw)
            with self.assertRaises(ValueError):
                trainer.prepare_daycare_egg()
            mem.put(address, original)
        for address, raw in ((DAYCARE + 1, b"\x42"),
                             (FLAG, b"\x00"),
                             (SAVE_POINTER, struct.pack("<I", SAVE1 + 0x1000))):
            prepared = trainer.prepare_daycare_egg()
            original = mem.read(address, len(raw))
            mem.put(address, raw)
            with self.assertRaises(ValueError):
                trainer.commit_daycare_egg(prepared)
            self.assertEqual(mem.writes, 0)
            mem.put(address, original)

    def test_restore_requires_unchanged_full_dependency_and_latest_receipt(self):
        trainer, mem, profile = self.fixture("4755f497")
        first = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
        original = mem.read(DAYCARE + 0x88, 4)
        mem.put(DAYCARE + 0x88, b"\x14\x02\x00\x00")
        with self.assertRaises(ValueError):
            trainer.restore(first["backup"])
        self.assertEqual(mem.read(FLAG, 1), b"\xc0")
        mem.put(DAYCARE + 0x88, original)
        # A consumed receipt cannot be retried, even if game bytes return.
        with self.assertRaises(ValueError):
            trainer.restore(first["backup"])
        mem.put(FLAG, b"\x80")  # Simulate the NPC collecting the first egg.
        second = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
        with self.assertRaises(ValueError):
            trainer.restore(first["backup"])
        self.assertEqual(mem.read(FLAG, 1), b"\xc0")
        other = Trainer(mem, profile, self.temp.name)
        with self.assertRaises(ValueError):
            other.restore(second["backup"])
        self.assertEqual(mem.read(FLAG, 1), b"\xc0")
        trainer.restore(second["backup"])
        self.assertEqual(mem.read(FLAG, 1), b"\x80")

    def test_tampered_backup_and_unconfirmed_status_never_clear_pending(self):
        trainer, mem, _ = self.fixture("b4af11c8")
        result = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
        backup = Path(result["backup"])
        original = backup.read_bytes()
        record = json.loads(original)
        record["patches"][0]["address"] += 1
        failed = dict(record, status="failed-or-unconfirmed")
        failed_path = Path(self.temp.name, "unconfirmed-daycare.json")
        failed_path.write_text(json.dumps(failed), encoding="utf-8")
        with self.assertRaises(ValueError):
            trainer.restore(failed_path)
        backup.write_text(json.dumps(record), encoding="utf-8")
        count = mem.writes
        with self.assertRaises(ValueError):
            trainer.restore(backup)
        self.assertEqual(mem.writes, count)
        self.assertEqual(mem.read(FLAG, 1), b"\xc0")
        backup.write_bytes(original)
        with self.assertRaises(ValueError):
            trainer.restore(backup)
        self.assertEqual(mem.writes, count)


@unittest.skipIf(test_end_to_end.LuaRuntime is None, "lupa is required")
class DaycareLuaTcpTests(unittest.TestCase):
    def setUp(self):
        test_end_to_end.EndToEndTests.setUp(self)

    def test_both_versions_actual_callback_write_verify_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                profile = load_profile(crc=crc)
                install_profile(self.memory, profile)
                install_daycare(self.memory)
                trainer = Trainer(self.client, profile, self.temp.name)
                sent = []
                self.after_command = lambda line: sent.append(line) if line.startswith(b"BATCHVERIFYCRC") else None
                result = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
                self.assertEqual(self.client.read(FLAG, 1), b"\xc0")
                trainer.restore(result["backup"])
                self.assertEqual(self.client.read(FLAG, 1), b"\x80")
                self.assertEqual(len(sent), 2)

    def test_callback_races_refuse_commit_and_restore_without_extra_writes(self):
        original_handle = self.handle
        for crc in ("b4af11c8", "4755f497"):
            profile = load_profile(crc=crc)
            cases = (
                ("parent_one_tail", DAYCARE + 0x8B, b"\x01"),
                ("parent_two_tail", DAYCARE + 0x117, b"\x01"),
                ("offspring_token", DAYCARE + 0x118, b"\x01"),
                ("step_counter", DAYCARE + 0x11A, b"\x01"),
                ("flag_other_bit", FLAG, b"\x81"),
                ("saveblock_pointer", SAVE_POINTER,
                 struct.pack("<I", SAVE1 + 0x1000)),
                ("battle", profile["battle_flag"]["address"],
                 bytes((profile["battle_flag"]["mask"],))),
                ("party_count", PARTY_COUNT, b"\x02"),
            )
            for label, address, changed in cases:
                with self.subTest(crc=crc, field=label, phase="commit"):
                    install_profile(self.memory, profile)
                    install_daycare(self.memory)
                    self.memory.put(profile["battle_flag"]["address"], b"\0")
                    self.memory.put(PARTY_COUNT, b"\x01")
                    trainer = Trainer(self.client, profile, self.temp.name)
                    prepared = trainer.prepare_daycare_egg()
                    count = self.memory.writes

                    def mutate_before_callback(line, *, a=address, value=changed):
                        if line.startswith(b"BATCHVERIFYCRC"):
                            self.memory.put(a, value)
                        return original_handle(line)

                    self.handle = mutate_before_callback
                    try:
                        with self.assertRaises(OSError):
                            trainer.commit_daycare_egg(prepared)
                    finally:
                        self.handle = original_handle
                    self.assertEqual(self.memory.writes, count)
                with self.subTest(crc=crc, field=label, phase="restore"):
                    install_profile(self.memory, profile)
                    install_daycare(self.memory)
                    self.memory.put(profile["battle_flag"]["address"], b"\0")
                    self.memory.put(PARTY_COUNT, b"\x01")
                    trainer = Trainer(self.client, profile, self.temp.name)
                    result = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
                    restore_changed = b"\xc1" if label == "flag_other_bit" else changed
                    count = self.memory.writes

                    def mutate_restore_callback(line, *, a=address,
                                                    value=restore_changed):
                        if line.startswith(b"BATCHVERIFYCRC"):
                            self.memory.put(a, value)
                        return original_handle(line)

                    self.handle = mutate_restore_callback
                    try:
                        with self.assertRaises(OSError):
                            trainer.restore(result["backup"])
                    finally:
                        self.handle = original_handle
                    self.assertEqual(self.memory.writes, count)

    def test_ack_loss_is_unconfirmed_and_aba_rejects_old_backup(self):
        original_handle = self.handle
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc, case="ack_loss"):
                profile = load_profile(crc=crc)
                install_profile(self.memory, profile)
                install_daycare(self.memory)
                trainer = Trainer(self.client, profile, self.temp.name)
                previous_backups = set(Path(self.temp.name).glob("*.json"))

                def lose_ack(line):
                    response = original_handle(line)
                    if line.startswith(b"BATCHVERIFYCRC") and response.startswith(b"OK"):
                        return b"ERR simulated lost acknowledgement"
                    return response

                self.handle = lose_ack
                try:
                    with self.assertRaises(OSError):
                        trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
                finally:
                    self.handle = original_handle
                self.assertEqual(self.client.read(FLAG, 1), b"\xc0")
                candidates = set(Path(self.temp.name).glob("*.json")) - previous_backups
                self.assertEqual(len(candidates), 1)
                failed = candidates.pop()
                self.assertEqual(json.loads(failed.read_text(encoding="utf-8"))["status"],
                                 "failed-or-unconfirmed")
                with self.assertRaises(ValueError):
                    trainer.restore(failed)
            with self.subTest(crc=crc, case="aba"):
                install_profile(self.memory, profile)
                install_daycare(self.memory)
                trainer = Trainer(self.client, profile, self.temp.name)
                first = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
                self.memory.put(FLAG, b"\x80")  # Native NPC collected it.
                second = trainer.commit_daycare_egg(trainer.prepare_daycare_egg())
                count = self.memory.writes
                with self.assertRaises(ValueError):
                    trainer.restore(first["backup"])
                self.assertEqual(self.memory.writes, count)
                self.assertEqual(self.client.read(FLAG, 1), b"\xc0")
                trainer.restore(second["backup"])
                self.assertEqual(self.client.read(FLAG, 1), b"\x80")


if __name__ == "__main__":
    unittest.main()
