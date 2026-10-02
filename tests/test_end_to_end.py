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

    def test_pc_ability_write_restore_over_actual_lua_tcp(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        for signature in self.profile["storage"]["signatures"]:
            self.memory.put(signature["address"], bytes.fromhex(signature["hex"]))
        raw = bytearray(packed_box())
        struct.pack_into("<H", raw, 28, 133)
        raw[39:44] = (757).to_bytes(5, "little")
        raw[57] &= 127
        address = self.profile["storage"]["box_addresses"][24]
        self.memory.put(address, raw)
        source = BoxPokemon(bytes(raw))
        for slot in [1, 2, 0]:
            snapshot = self.trainer.snapshot_box(24)
            patches, _ = self.trainer.edit_box(snapshot, 0, ability_slot=slot)
            result = self.trainer.commit_box(patches, "PC ability integration")
            updated = BoxPokemon(self.client.read(address, 58))
            self.assertEqual(updated.ability_flag, int(slot == 2))
            self.assertEqual(updated.pid % 25, source.pid % 25)
            self.assertEqual(updated.shiny, source.shiny)
            self.assertEqual(updated.ivs, source.ivs)
            self.assertEqual(updated.egg, source.egg)
            if slot < 2:
                self.assertEqual(updated.pid & 1, slot)
            if result["changed"]:
                self.trainer.restore(result["backup"])
            else:
                self.assertIsNone(result["backup"])
            self.assertEqual(self.client.read(address, 58), bytes(raw))

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

    def test_chinese_player_name_and_ids_write_restore_over_actual_lua_tcp(self):
        pointer = self.profile["trainer"]["pointer_address"]
        address = 0x2024588
        original = (
            b"\x06\x02" + b"\xff" * 6 + b"\x01\xa5" + struct.pack("<HH", 12345, 54321)
        )
        self.memory.put(pointer, struct.pack("<I", address))
        self.memory.put(address, original)
        snapshot = self.trainer.snapshot_trainer()
        result = self.trainer.commit_trainer_profile(snapshot, 65535, 0, "大力鳄A")
        current = self.trainer.snapshot_trainer()
        self.assertEqual(
            (current["name"], current["tid"], current["sid"]), ("大力鳄A", 65535, 0)
        )
        self.assertEqual(self.client.read(address + 8, 2), original[8:10])
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 14), original)

    def test_nature_form_and_details_write_restore_over_actual_lua_tcp(self):
        from tests.test_box import packed_box

        initial = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(
            initial, 0, species=1141, nature=0, ability_slot=1
        )
        original = Pokemon(patches[0][2])
        self.memory.put(PARTY, original.raw)
        snapshot = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(
            snapshot,
            0,
            nature=1,
            shiny=True,
            ot_name="大力鳄A",
            met_location=213,
            nickname="大力鳄小智",
        )
        result = self.trainer.commit(snapshot, patches, "nature form integration")
        updated = Pokemon(self.client.read(PARTY, 100))
        self.assertEqual(
            (
                updated.species,
                updated.pid % 25,
                updated.shiny,
                updated.ot_name,
                updated.met_location,
                updated.nickname,
            ),
            (1193, 1, True, "大力鳄A", 213, "大力鳄小智"),
        )
        self.assertEqual(updated.experience, original.experience)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(PARTY, 100), original.raw)

        for signature in self.profile["storage"]["signatures"]:
            self.memory.put(signature["address"], bytes.fromhex(signature["hex"]))
        raw = bytearray(packed_box())
        struct.pack_into("<I", raw, 0, original.pid)
        struct.pack_into("<H", raw, 28, 1141)
        raw[39:44] = (757).to_bytes(5, "little")
        raw[57] &= 127
        address = self.profile["storage"]["box_addresses"][24]
        self.memory.put(address, raw)
        snapshot = self.trainer.snapshot_box(24)
        patches, _ = self.trainer.edit_box(
            snapshot,
            0,
            nature=1,
            shiny=True,
            ot_name="大力鳄A",
            met_location=213,
            nickname="大力鳄小智",
        )
        result = self.trainer.commit_box(patches, "PC nature form integration")
        self.assertEqual(self.client.read(address, 58), patches[0][2])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 58), bytes(raw))

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

    def test_item_dependent_forms_write_readback_restore_over_actual_lua_tcp(self):
        from tests.test_box import packed_box

        initial = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(initial, 0, species=546)
        original = patches[0][2]
        self.memory.put(PARTY, original)
        snapshot = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snapshot, 0, held=490)
        result = self.trainer.commit(snapshot, patches, "held form integration")
        self.assertEqual(Pokemon(self.client.read(PARTY, 100)).species, 720)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(PARTY, 100), original)
        for signature in self.profile["storage"]["signatures"]:
            self.memory.put(signature["address"], bytes.fromhex(signature["hex"]))
        raw = bytearray(packed_box())
        struct.pack_into("<H", raw, 28, 990)
        raw[39:44] = (757).to_bytes(5, "little")
        address = self.profile["storage"]["box_addresses"][24]
        self.memory.put(address, raw)
        snapshot = self.trainer.snapshot_box(24)
        patches, _ = self.trainer.edit_box(snapshot, 0, held=507)
        result = self.trainer.commit_box(patches, "PC held form integration")
        self.assertEqual(self.client.read(address, 58), patches[0][2])
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 58), bytes(raw))


if __name__ == "__main__":
    unittest.main()
