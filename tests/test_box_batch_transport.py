"""Whole PC batch operations through real Lua framing and a TCP client."""

import json
import unittest
from pathlib import Path

from tests.test_box_sort_lock import record
from tests import test_end_to_end
from tests.test_bridge import LuaRuntime
from tests.test_rom_versions import install_profile
from rom_versions import load_profile
from trainer_core import Trainer


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class BoxBatchTransportTests(unittest.TestCase):
    def setUp(self):
        test_end_to_end.EndToEndTests.setUp(self)
        self.sent = []
        self.after_command = lambda line: (
            self.sent.append(line) if line.startswith(b"BOXBATCHCRC ") else None
        )

    def use_version(self, crc):
        profile = load_profile(crc=crc)
        install_profile(self.memory, profile)
        self.trainer = Trainer(self.client, profile, self.temp.name)
        return profile["storage"]["box_addresses"]

    def fill(self, addresses, *, eggs=False):
        originals = []
        for box, address in enumerate(addresses):
            members = []
            for slot in range(30):
                raw = bytearray(record(160, box * 30 + slot + 1))
                if eggs:
                    raw[19] |= 4
                    raw[57] |= 64
                    raw[37] = 1 + (slot % 20)
                members.append(bytes(raw))
            full = b"".join(members)
            originals.append(full)
            self.memory.put(address, full)
        return originals

    def references(self):
        return [
            self.trainer.box_reference(snapshot, slot)
            for box in range(25)
            for snapshot in (self.trainer.snapshot_box(box),)
            for slot in range(30)
        ]

    def assert_one_large_backup(self, result):
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith(b"BOXBATCHCRC "))
        self.assertLessEqual(len(self.sent[0]), 270000)
        record_data = json.loads(Path(result["backup"]).read_text("utf-8"))
        self.assertEqual(record_data["status"], "verified")
        self.assertEqual(len(record_data["patches"]), 25)

    def test_two_versions_full_25_box_move_single_callback_and_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                addresses = self.use_version(crc)
                originals = self.fill(addresses)
                refs = self.references()
                prepared = self.trainer.prepare_box_batch(list(reversed(refs)), "move", 0)
                self.assertEqual(len(prepared["patches"]), 25)
                result = self.trainer.commit_box_batch(prepared)
                self.assert_one_large_backup(result)
                self.assertEqual(
                    [self.memory.read(a + s * 58, 58) for a in addresses for s in range(30)],
                    [ref["raw"] for ref in reversed(refs)],
                )
                changed = self.memory.read(addresses[0] + 18, 1)
                self.memory.put(addresses[0] + 18, bytes([changed[0] ^ 1]))
                with self.assertRaisesRegex(OSError, "游戏数据已变化"):
                    self.trainer.restore(result["backup"])
                self.memory.put(addresses[0] + 18, changed)
                self.trainer.restore(result["backup"])
                self.assertEqual([self.memory.read(a, 1740) for a in addresses], originals)
                self.sent.clear()

    def test_two_versions_full_25_box_existing_eggs_zero_cycles_and_restore(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc):
                addresses = self.use_version(crc)
                originals = self.fill(addresses, eggs=True)
                refs = self.references()
                prepared = self.trainer.prepare_box_batch(refs, "egg")
                self.assertEqual(len(prepared["patches"]), 25)
                result = self.trainer.commit_box_batch(prepared)
                self.assert_one_large_backup(result)
                for address, original in zip(addresses, originals):
                    current = self.memory.read(address, 1740)
                    for slot in range(30):
                        before = original[slot * 58 : (slot + 1) * 58]
                        after = current[slot * 58 : (slot + 1) * 58]
                        self.assertEqual(after[:37] + after[38:], before[:37] + before[38:])
                        self.assertEqual(after[37], 0)
                self.trainer.restore(result["backup"])
                self.assertEqual([self.memory.read(a, 1740) for a in addresses], originals)
                self.sent.clear()

    def test_lua_stale_guard_and_partial_failure_keep_unconfirmed_backup(self):
        addresses = self.use_version("4755f497")
        self.fill(addresses)
        prepared = self.trainer.prepare_box_batch(list(reversed(self.references())), "move", 0)
        self.memory.put(addresses[24] + 18, b"\xa5")
        before = [self.memory.read(a, 1740) for a in addresses]
        with self.assertRaisesRegex(OSError, "游戏数据已变化"):
            self.trainer.commit_box_batch(prepared)
        self.assertEqual([self.memory.read(a, 1740) for a in addresses], before)
        self.assertEqual(self.memory.writes, 0)
        backups = list(Path(self.temp.name).glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text("utf-8"))["status"], "failed-or-unconfirmed")
        with self.assertRaisesRegex(ValueError, "未被确认"):
            self.trainer.restore(backups[0])

        self.memory.put(addresses[24] + 18, prepared["patches"][-1][1][18:19])
        self.sent.clear()
        write_count = [0]

        def interrupted_write(_, address, value):
            write_count[0] += 1
            if write_count[0] <= 100:
                self.memory.put(address, bytes([value]))
                self.memory.writes += 1

        self.lua.globals()[b"emu"][b"write8"] = interrupted_write
        with self.assertRaisesRegex(OSError, "readback"):
            self.trainer.commit_box_batch(prepared)
        self.assertEqual(len(self.sent), 1)
        self.assertGreater(self.memory.writes, 0)
        backups = list(Path(self.temp.name).glob("*.json"))
        self.assertEqual(len(backups), 2)
        for backup in backups:
            self.assertEqual(json.loads(backup.read_text("utf-8"))["status"], "failed-or-unconfirmed")
            with self.assertRaisesRegex(ValueError, "未被确认"):
                self.trainer.restore(backup)


if __name__ == "__main__":
    unittest.main()
