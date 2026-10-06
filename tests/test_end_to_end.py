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
    def test_gender_and_pp_ups_write_readback_restore_over_actual_lua_tcp(self):
        from rom_versions import load_profile
        from tests.test_rom_versions import install_profile
        from pokemon_data import gender
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        for crc in ("b4af11c8", "4755f497"):
            profile = load_profile(crc=crc)
            install_profile(self.memory, profile)
            trainer = Trainer(self.client, profile, self.temp.name)
            snapshot = trainer.snapshot()
            patches, _ = trainer.edit_pokemon(
                snapshot, 0, target_gender="雌性", pp_ups=[3] * 4
            )
            result = trainer.commit(snapshot, patches, "gender/PP")
            updated = Pokemon(self.client.read(PARTY, 100))
            self.assertEqual(updated.pp_ups, (3, 3, 3, 3))
            self.assertEqual(gender(updated.pid, 31), "雌性")
            trainer.restore(result["backup"])
            self.assertEqual(self.client.read(PARTY, 100), sample().raw)
            raw = bytearray(packed_box())
            raw[39:44] = sum(
                m << (10 * i) for i, m in enumerate((757, 242, 8, 700))
            ).to_bytes(5, "little")
            address = profile["storage"]["box_addresses"][0]
            self.memory.put(address, bytes(raw) + b"\0" * 58 * 29)
            box = trainer.snapshot_box(0)
            patches, _ = trainer.edit_box(
                box, 0, target_gender="雌性", pp_ups=[3, 2, 1, 0]
            )
            result = trainer.commit_box(patches, "PC gender/PP")
            updated = BoxPokemon(self.client.read(address, 58))
            self.assertEqual(updated.pp_ups, (3, 2, 1, 0))
            self.assertEqual(gender(updated.pid, 31), "雌性")
            trainer.restore(result["backup"])
            self.assertEqual(self.client.read(address, 58), bytes(raw))

    def test_all_25_boxes_sort_compare_in_one_large_lua_callback_and_restore(self):
        from tests.test_box_sort_lock import record
        from tests.test_rom_versions import install_profile

        install_profile(self.memory, self.profile)
        addresses = self.profile["storage"]["box_addresses"]
        originals = []
        for box, address in enumerate(addresses):
            raw = b"".join(
                record(160 if slot % 2 else 1, box * 30 + slot) for slot in range(30)
            )
            originals.append(raw)
            self.memory.put(address, raw)
        sent = []
        self.after_command = lambda line: (
            sent.append(line) if line.startswith(b"BOXBATCHCRC") else None
        )
        result = self.trainer.commit_box_sort(self.trainer.prepare_box_sort())
        self.assertEqual(len(sent), 1)
        self.assertGreater(len(sent[0]), 40000)
        self.assertLessEqual(len(sent[0]), 270000)
        actual = [
            self.memory.read(a + s * 58, 58) for a in addresses for s in range(30)
        ]
        expected = sorted(
            [raw[s : s + 58] for raw in originals for s in range(0, 1740, 58)],
            key=lambda raw: int.from_bytes(raw[28:30], "little"),
        )
        self.assertEqual(actual, expected)
        self.trainer.restore(result["backup"])
        self.assertEqual(len(sent), 2)
        self.assertEqual([self.memory.read(a, 1740) for a in addresses], originals)

        prepared = self.trainer.prepare_box_sort()
        self.memory.put(addresses[-1] + 18, b"\xa5")
        before = dict(self.memory.data)
        with self.assertRaisesRegex(OSError, "游戏数据已变化"):
            self.trainer.commit_box_sort(prepared)
        self.assertEqual(self.memory.data, before)

    def test_v12_party_pc_values_and_sort_use_new_guards_over_lua_tcp(self):
        from rom_versions import load_profile
        from tests.test_rom_versions import install_profile
        from tests.test_box import packed_box

        self.profile = load_profile(crc="4755f497")
        install_profile(self.memory, self.profile)
        self.trainer = Trainer(self.client, self.profile, self.temp.name)
        snapshot = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(
            snapshot, 0, shiny=True, egg=True, ivs=[31] * 6
        )
        result = self.trainer.commit(snapshot, patches, "V1.2 party")
        self.assertTrue(Pokemon(self.client.read(PARTY, 100)).egg)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)

        snapshot = self.trainer.snapshot()
        result = self.trainer.commit(
            snapshot,
            self.trainer.edit_values(snapshot, 100, 735, 5, 220),
            "V1.2 values",
        )
        self.assertEqual(self.trainer.snapshot()["coins"], 735)
        self.trainer.restore(result["backup"])

        pocket = self.profile["pockets"][0]
        raw = struct.pack("<4H", 13, 2, 4, 164) + b"\0" * (pocket["capacity"] * 4 - 8)
        self.memory.put(pocket["address"], raw)
        snapshot = self.trainer.snapshot()
        result = self.trainer.commit(
            snapshot, self.trainer.sort_bag(snapshot), "V1.2 sort"
        )
        self.assertEqual(
            self.client.read(pocket["address"], 4), struct.pack("<HH", 4, 164)
        )
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(pocket["address"], len(raw)), raw)

        address = self.profile["storage"]["box_addresses"][24]
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        raw = bytes(raw)
        self.memory.put(address, raw)
        snapshot = self.trainer.snapshot_box(24)
        patches, _ = self.trainer.edit_box(snapshot, 0, egg=True)
        result = self.trainer.commit_box(patches, "V1.2 PC egg")
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 58), raw)

        result = self.trainer.commit_box_move(
            self.trainer.snapshot_box(24), 0, self.trainer.snapshot_box(19)
        )
        destination = (
            self.profile["storage"]["box_addresses"][19] + result["target_slot"] * 58
        )
        self.assertEqual(self.client.read(address, 58), b"\0" * 58)
        self.assertEqual(self.client.read(destination, 58), raw)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(address, 58), raw)
        self.assertEqual(self.client.read(destination, 58), b"\0" * 58)

    def test_v12_rom_swap_blocks_transaction_before_any_byte_is_written(self):
        from rom_versions import load_profile
        from tests.test_rom_versions import install_profile

        self.profile = load_profile(crc="4755f497")
        install_profile(self.memory, self.profile)
        self.trainer = Trainer(self.client, self.profile, self.temp.name)
        snapshot = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snapshot, 0, shiny=True)
        self.memory.rom_crc32 = "b4af11c8"
        with self.assertRaises((ValueError, OSError)):
            self.trainer.commit(snapshot, patches, "ROM changed")
        self.assertEqual(self.memory.writes, 0)

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
            if getattr(self, "after_command", None):
                self.after_command(line)
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

    def test_playtime_write_and_restore_over_actual_lua_tcp(self):
        from tests.test_time import put_time

        put_time(self.memory, self.profile)
        snap = self.trainer.snapshot_time()
        result = self.trainer.commit_playtime(snap, 1, 2, 3)
        self.assertEqual(self.trainer.snapshot_time()["playtime"], (1, 2, 3, 255))
        self.trainer.restore(result["backup"])
        self.assertEqual(self.trainer.snapshot_time()["playtime"], (26, 4, 18, 255))
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)

    def test_playtime_readback_is_confirmed_before_next_frame_advances(self):
        from tests.test_time import put_time

        put_time(self.memory, self.profile)
        snapshot = self.trainer.snapshot_time()

        def advance(line):
            if line.startswith(b"BATCHVERIFYCRC"):
                self.memory.put(snapshot["address"] + 17, b"\x04")

        self.after_command = advance
        result = self.trainer.commit_playtime(snapshot, 1, 2, 3)
        record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "verified")
        self.assertEqual(self.trainer.snapshot_time()["playtime"][:3], (1, 2, 4))
        with self.assertRaisesRegex(OSError, "游戏数据已变化"):
            self.trainer.restore(result["backup"])

    def test_daily_repair_readback_and_restore_over_actual_lua_tcp(self):
        from tests.test_time import put_time

        put_time(self.memory, self.profile)
        snapshot = self.trainer.snapshot_time()
        result = self.trainer.commit_daily_repair(snapshot)
        self.assertEqual(self.trainer.snapshot_time()["daily_date"].day, 1)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.trainer.snapshot_time()["daily_date"].day, 4)
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)

    def test_all_values_write_restore_with_full_guards_over_actual_lua_tcp(self):
        economy = self.profile["economy"]
        for key, value in [
            ("coins", 735),
            ("beauty_points", 5),
            ("bracer_points", 220),
        ]:
            field = economy[key]
            self.memory.put(field["address"], value.to_bytes(field["size"], "little"))
        snapshot = self.trainer.snapshot()
        patches = self.trainer.edit_values(snapshot, 9999999, 999999999, 65535, 65535)
        result = self.trainer.commit(snapshot, patches, "all four values")
        after = self.trainer.snapshot()
        self.assertEqual(
            tuple(
                after[k] for k in ("money", "coins", "beauty_points", "bracer_points")
            ),
            (9999999, 999999999, 65535, 65535),
        )
        self.assertEqual(
            self.client.read(snapshot["saveblock"] + 0x294, 2),
            snapshot["money_raw"][4:6],
        )
        self.trainer.restore(result["backup"])
        for address, before, _ in patches:
            self.assertEqual(self.client.read(address, len(before)), before)

    def test_full_bag_sort_is_atomic_and_restores_over_actual_lua_tcp(self):
        p = self.profile["pockets"][0]
        raw = b"".join(
            struct.pack("<HH", 13 + (i % 9), i % 1000)
            for i in range(p["capacity"] - 1, -1, -1)
        )
        self.memory.put(p["address"], raw)
        snapshot = self.trainer.snapshot()
        patches = self.trainer.sort_bag(snapshot)
        self.memory.put(p["address"] + len(raw) - 1, bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(IOError, "游戏数据已变化"):
            self.trainer.commit(snapshot, patches, "stale tail")
        self.assertEqual(self.memory.writes, 0)
        self.memory.put(p["address"], raw)
        result = self.trainer.commit(snapshot, patches, "full sort")
        self.assertEqual(self.client.read(p["address"], len(raw)), patches[0][2])
        self.assertEqual(self.client.read(PARTY, 100), sample().raw)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.client.read(p["address"], len(raw)), raw)

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
