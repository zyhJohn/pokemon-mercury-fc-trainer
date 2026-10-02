"""Actual TCP framing + actual Lua BATCH + editor + backup, with simulated RAM."""

import json
import struct
import tempfile
import unittest
from pathlib import Path
from tests.test_bridge import LuaRuntime
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_transport import Server
from memory_client import MemClient
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from pokemon_data import Pokemon


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory()
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        for s in self.profile["signatures"]:
            self.memory.put(s["address"], bytes.fromhex(s["hex"]))
        self.memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.memory.put(PARTY_COUNT, b"\1")
        self.memory.put(PARTY, sample().raw)
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        emu = self.lua.table()
        emu[b"readRange"] = lambda _, a, n: self.memory.read(a, n)

        def write(_, a, v):
            self.memory.put(a, bytes([v]))
            self.memory.writes += 1

        emu[b"write8"] = write
        emu[b"checksum"] = lambda _: bytes.fromhex(self.memory.rom_crc32)
        self.lua.globals()[b"emu"] = emu
        self.lua.globals()[b"MERCURY_BRIDGE_TEST"] = True
        self.handle = self.lua.execute(Path("mercury_bridge.lua").read_bytes())

        def reply(line):
            result = self.handle(line) + b"\n"
            return [result[:3], result[3:]]

        self.server = Server(reply)
        self.addCleanup(self.server.join)
        self.client = MemClient(port=self.server.port)
        self.addCleanup(self.client.close)
        self.client.connect(scan=1)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trainer = Trainer(self.client, self.profile, self.temp.name)

    def test_shiny_iv_write_readback_and_restore_over_tcp(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(
            snap, 0, shiny=True, ivs=[31] * 6, evs=[252, 252, 0, 0, 0, 6]
        )
        record = self.trainer.commit(snap, patches, "TCP and actual Lua")
        updated = Pokemon(self.client.read(PARTY, 100))
        self.assertTrue(updated.shiny)
        self.assertEqual(updated.ivs, (31,) * 6)
        self.assertEqual(updated.ability_flag, sample().ability_flag)
        self.trainer.restore(record["backup"])
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)

    def test_change_after_snapshot_refuses_all_writes(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        self.memory.put(PARTY + 90, b"\xff")
        with self.assertRaisesRegex(IOError, "游戏数据已变化"):
            self.trainer.commit(snap, patches, "stale")
        self.assertEqual(self.memory.writes, 0)
        record = json.loads(
            next(Path(self.temp.name).glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(record["status"], "failed-or-unconfirmed")

    def test_pc_edit_and_restore_over_crc_guarded_tcp(self):
        from tests.test_box import packed_box

        for s in self.profile["storage"]["signatures"]:
            self.memory.put(s["address"], bytes.fromhex(s["hex"]))
        raw = bytearray(packed_box())
        raw[39:44] = sum(
            m << (i * 10) for i, m in enumerate([757, 242, 8, 700])
        ).to_bytes(5, "little")
        address = self.profile["storage"]["box_addresses"][24]
        self.memory.put(address, raw)
        snapshot = self.trainer.snapshot_box(24)
        patches, _ = self.trainer.edit_box(snapshot, 0, ivs=[31] * 6, shiny=True)
        result = self.trainer.commit_box(patches, "PC integration")
        self.assertEqual(self.client.read(address, 58), patches[0][2])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 58), bytes(raw))


if __name__ == "__main__":
    unittest.main()
