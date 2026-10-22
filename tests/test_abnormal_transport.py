"""Abnormal deletion over actual Lua/TCP with synthetic RAM, never live mGBA."""

import copy
import json
import struct
import unittest
from functools import partial
from pathlib import Path

from pokemon_data import Pokemon
from pokemon_deletion_layout import party_deletion_layout
from rom_versions import load_profile
from tests import test_end_to_end as transport
from tests.test_box_sort_lock import record
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer

CRCS = ("b4af11c8", "4755f497", "518482f7")


def bad_flag(raw):
    altered = bytearray(raw)
    altered[19] |= 1
    return bytes(altered)


@unittest.skipIf(transport.LuaRuntime is None, "lupa is required")
class AbnormalTransportTests(unittest.TestCase):
    def setUp(self):
        # Construction only; do not inherit or rediscover the other suite.
        transport.EndToEndTests.setUp(self)
        self.base_handler = self.server.handler
        self.original_write = self.lua.globals()[b"emu"][b"write8"]

    def use_release(self, crc):
        self.memory = Memory()
        self.profile = load_profile(crc=crc)
        install_profile(self.memory, self.profile)
        self.memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.memory.put(PARTY_COUNT, b"\x03")
        self.party = (
            sample(pid=0x43DEB981).raw
            + bad_flag(sample(pid=0x43DEB983).raw)
            + sample(pid=0x43DEB985).raw
            + bytes([0xA5]) * 300
        )
        self.memory.put(PARTY, self.party)
        self.deletion_layout = party_deletion_layout(self.profile)
        self.memory.put(
            self.deletion_layout["callback_address"], self.deletion_layout["callbacks"]
        )
        self.memory.put(self.deletion_layout["script_lock_address"], b"\0")
        for address, raw in self.deletion_layout["code_signatures"]:
            self.memory.put(address, raw)
        self.commands = []
        self.after_command = self.commands.append
        self.server.handler = self.base_handler
        self.lua.globals()[b"emu"][b"write8"] = self.original_write
        self.trainer = Trainer(self.client, self.profile, Path(self.temp.name) / crc)

    def before_batch(self, callback):
        seen = []

        def handle(line):
            if line.startswith(b"BATCHVERIFYCRC ") and not seen:
                seen.append(line)
                callback(line)
            return self.base_handler(line)

        self.server.handler = handle
        return seen

    def assert_verified_backup(self, result, kind):
        path = Path(result["backup"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "verified")
        self.assertEqual(data["rom_identity"], self.profile["rom_sha256"])
        self.assertEqual(data["abnormal_context"]["direction"], "delete")
        self.assertEqual(data["abnormal_context"]["plan"]["kind"], kind)
        self.assertTrue(
            any(line.startswith(b"BATCHVERIFYCRC ") for line in self.commands)
        )
        return path, data

    def test_party_delete_compacts_and_restores_all_six_records_three_baselines(self):
        for crc in CRCS:
            for slot in range(3):
                with self.subTest(crc=crc, slot=slot):
                    self.use_release(crc)
                    records = [sample(pid=0x43DEB981 + 2 * i).raw for i in range(3)]
                    records[slot] = bad_flag(records[slot])
                    original = b"".join(records) + bytes([0xA5]) * 300
                    self.memory.put(PARTY, original)
                    self.memory.put(PARTY_COUNT - 1, b"\xd1\x03\xd2\xd3")
                    prepared = self.trainer.prepare_abnormal_deletion("party", slot)
                    durable = []

                    def backup_exists(_, durable=durable):
                        backups = list(self.trainer.backup_dir.glob("*.json"))
                        active = [
                            json.loads(path.read_text(encoding="utf-8"))
                            for path in backups
                        ]
                        durable.extend(
                            item for item in active if item["status"] == "prepared"
                        )

                    seen = self.before_batch(backup_exists)
                    result = self.trainer.commit_abnormal_deletion(prepared)
                    expected = (
                        b"".join(records[:slot] + records[slot + 1 :])
                        + bytes(100)
                        + original[300:]
                    )
                    self.assertEqual(self.client.read(PARTY, 600), expected)
                    self.assertEqual(
                        self.client.read(PARTY_COUNT - 1, 4), b"\xd1\x02\xd2\xd3"
                    )
                    self.assertEqual(len(seen), 1)
                    wire = {
                        int(address, 16): (
                            bytes.fromhex(before.decode()),
                            bytes.fromhex(after.decode()),
                        )
                        for address, before, after in (
                            part.split(b":")
                            for part in seen[0].split(b" ", 2)[2].split(b";")
                        )
                    }
                    self.assertEqual(wire[PARTY_COUNT], (b"\x03", b"\x02"))
                    self.assertEqual(
                        wire[self.deletion_layout["callback_address"]],
                        (
                            self.deletion_layout["callbacks"],
                            self.deletion_layout["callbacks"],
                        ),
                    )
                    self.assertEqual(
                        wire[self.deletion_layout["script_lock_address"]],
                        (b"\0", b"\0"),
                    )
                    for address, expected_code in self.deletion_layout[
                        "code_signatures"
                    ]:
                        self.assertEqual(wire[address], (expected_code, expected_code))
                    for member in range(6):
                        start = member * 100
                        self.assertEqual(
                            wire[PARTY + start],
                            (
                                original[start : start + 100],
                                expected[start : start + 100],
                            ),
                        )
                    self.assertTrue(durable)
                    path, backup = self.assert_verified_backup(result, "party")
                    self.assertEqual(
                        backup["abnormal_context"]["plan"]["before"], original.hex()
                    )
                    self.assertEqual(
                        backup["abnormal_context"]["plan"]["after"], expected.hex()
                    )
                    self.trainer.restore(path)
                    self.assertEqual(self.client.read(PARTY, 600), original)
                    self.assertEqual(
                        self.client.read(PARTY_COUNT - 1, 4), b"\xd1\x03\xd2\xd3"
                    )
                    with self.assertRaisesRegex(ValueError, "失效"):
                        self.trainer.commit_abnormal_deletion(prepared)

    def test_pc_bad_flags_and_unknown_residue_delete_only_target_and_restore_three_baselines(
        self,
    ):
        for crc in CRCS:
            for damaged in (bad_flag(record(160, 42)), b"\x01" + bytes(57)):
                with self.subTest(crc=crc, residue=damaged[28:30] == bytes(2)):
                    self.use_release(crc)
                    box = 24
                    address = self.profile["storage"]["box_addresses"][box]
                    original = (
                        b"".join(record(160, i + 100) for i in range(29)) + damaged
                    )
                    self.memory.put(address, original)
                    before = dict(self.memory.data)
                    prepared = self.trainer.prepare_abnormal_deletion("box", 29, box)
                    result = self.trainer.commit_abnormal_deletion(prepared)
                    self.assertEqual(
                        self.client.read(address, 1740), original[:-58] + bytes(58)
                    )
                    self.assertEqual(self.client.read(PARTY, 600), self.party)
                    changed = {
                        key for key in before if before[key] != self.memory.data[key]
                    }
                    self.assertTrue(
                        changed <= set(range(address + 29 * 58, address + 30 * 58))
                    )
                    path, backup = self.assert_verified_backup(result, "box")
                    self.assertEqual(
                        backup["abnormal_context"]["plan"]["before"], damaged.hex()
                    )
                    self.trainer.restore(path)
                    self.assertEqual(self.memory.data, before)

    def test_ordinary_edits_cannot_bypass_isolation_but_healthy_members_remain_editable(
        self,
    ):
        for crc in CRCS:
            with self.subTest(crc=crc):
                self.use_release(crc)
                address = self.profile["storage"]["box_addresses"][0]
                bad_box, healthy_box = bad_flag(record(160, 42)), record(160, 43)
                self.memory.put(address, bad_box + healthy_box)
                snapshot = self.trainer.snapshot()
                box = self.trainer.snapshot_box(0)
                before = self.memory.writes
                for callback in (
                    partial(self.trainer.edit_pokemon, snapshot, 1, ivs=[31] * 6),
                    partial(
                        self.trainer.commit,
                        snapshot,
                        [(PARTY + 100, self.party[100:200], sample().raw)],
                        "bypass party",
                    ),
                    partial(self.trainer.edit_box, box, 0, friendship=99),
                    partial(
                        self.trainer.commit_box,
                        [(address, bad_box, healthy_box)],
                        "bypass box",
                    ),
                    partial(
                        self.trainer.commit_box_move,
                        box,
                        0,
                        self.trainer.snapshot_box(1),
                    ),
                ):
                    with self.assertRaises(ValueError):
                        callback()
                self.assertEqual(self.memory.writes, before)
                patches, _ = self.trainer.edit_pokemon(snapshot, 0, ivs=[31] * 6)
                result = self.trainer.commit(snapshot, patches, "healthy survivor edit")
                self.assertEqual(Pokemon(self.client.read(PARTY, 100)).ivs, (31,) * 6)
                self.assertEqual(
                    self.client.read(PARTY + 100, 100), self.party[100:200]
                )
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(PARTY, 600), self.party)
                box = self.trainer.snapshot_box(0)
                patches, _ = self.trainer.edit_box(box, 1, friendship=99)
                result = self.trainer.commit_box(patches, "healthy PC edit")
                self.assertEqual(self.client.read(address, 58), bad_box)
                self.trainer.restore(result["backup"])
                self.assertEqual(self.client.read(address, 116), bad_box + healthy_box)

    def test_callback_races_reject_all_writes_for_survivor_pointer_and_battle(self):
        for crc in CRCS:
            for race in (
                "survivor",
                "inactive",
                "save_pointer",
                "battle",
                "rom",
                "script_lock",
                "callback_initialization",
                "map_code",
            ):
                with self.subTest(crc=crc, race=race):
                    self.use_release(crc)
                    prepared = self.trainer.prepare_abnormal_deletion("party", 1)
                    expected = []

                    def mutate(_, race=race, expected=expected):
                        if race == "survivor":
                            self.memory.put(PARTY + 5, b"\x99")
                        elif race == "inactive":
                            self.memory.put(PARTY + 599, b"\x99")
                        elif race == "save_pointer":
                            self.memory.put(SAVE_POINTER, struct.pack("<I", 0x02026000))
                        elif race == "battle":
                            flag = self.profile["battle_flag"]
                            self.memory.put(flag["address"], bytes([flag["mask"]]))
                        elif race == "rom":
                            self.memory.rom_crc32 = "00000000"
                        else:
                            self.change_field_state(race)
                            flag = self.profile["battle_flag"]
                            self.assertFalse(
                                self.memory.r8(flag["address"]) & flag["mask"]
                            )
                        expected.append(dict(self.memory.data))

                    seen = self.before_batch(mutate)
                    with self.assertRaises(OSError):
                        self.trainer.commit_abnormal_deletion(prepared)
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(self.memory.data, expected[0])
                    self.assertEqual(self.memory.writes, 0)

    def change_field_state(self, mode):
        if mode == "script_lock":
            # Start menu keeps CB1/CB2 unchanged and locks ScriptContext2.
            self.memory.put(self.deletion_layout["script_lock_address"], b"\1")
        elif mode == "callback_initialization":
            self.memory.put(self.deletion_layout["callback_address"], bytes(8))
        elif mode == "map_code":
            address, raw = self.deletion_layout["code_signatures"][0]
            self.memory.put(address, bytes([raw[0] ^ 1]))
        else:
            raise AssertionError(mode)

    def test_non_field_menu_lock_and_tampered_native_guard_refuse_preview_and_restore(
        self,
    ):
        for crc in CRCS:
            for mode in ("script_lock", "callback_initialization", "map_code"):
                with self.subTest(crc=crc, mode=mode):
                    self.use_release(crc)
                    self.change_field_state(mode)
                    before = dict(self.memory.data)
                    with self.assertRaisesRegex(ValueError, "普通地图|守卫代码"):
                        self.trainer.prepare_abnormal_deletion("party", 1)
                    self.assertEqual(self.memory.data, before)
                    self.assertEqual(self.memory.writes, 0)

                    self.use_release(crc)
                    prepared = self.trainer.prepare_abnormal_deletion("party", 1)
                    result = self.trainer.commit_abnormal_deletion(prepared)
                    self.change_field_state(mode)
                    before = dict(self.memory.data)
                    writes = self.memory.writes
                    batches = sum(
                        line.startswith(b"BATCHVERIFYCRC ") for line in self.commands
                    )
                    with self.assertRaisesRegex(ValueError, "普通地图|守卫代码"):
                        self.trainer.restore(result["backup"])
                    self.assertEqual(self.memory.data, before)
                    self.assertEqual(self.memory.writes, writes)
                    self.assertEqual(
                        sum(
                            line.startswith(b"BATCHVERIFYCRC ")
                            for line in self.commands
                        ),
                        batches,
                    )

    def test_restoration_callback_race_rejects_menu_lock_and_initializing_callbacks(
        self,
    ):
        for crc in CRCS:
            for mode in ("script_lock", "callback_initialization"):
                with self.subTest(crc=crc, mode=mode):
                    self.use_release(crc)
                    prepared = self.trainer.prepare_abnormal_deletion("party", 1)
                    result = self.trainer.commit_abnormal_deletion(prepared)
                    expected = []

                    def enter_menu(_, mode=mode, expected=expected):
                        self.change_field_state(mode)
                        expected.append(dict(self.memory.data))

                    writes = self.memory.writes
                    seen = self.before_batch(enter_menu)
                    with self.assertRaises(OSError):
                        self.trainer.restore(result["backup"])
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(self.memory.data, expected[0])
                    self.assertEqual(self.memory.writes, writes)

    def test_partial_write_fails_real_lua_readback_and_cannot_replay_or_restore(self):
        for crc in CRCS:
            with self.subTest(crc=crc):
                self.use_release(crc)
                address = self.profile["storage"]["box_addresses"][0]
                original = bad_flag(record(160, 0x12345678))
                self.memory.put(address, original)
                prepared = self.trainer.prepare_abnormal_deletion("box", 0, 0)
                attempts = []

                def partial_write(_, target, value, attempts=attempts):
                    attempts.append(target)
                    if len(attempts) <= 3:
                        self.original_write(_, target, value)

                self.lua.globals()[b"emu"][b"write8"] = partial_write
                with self.assertRaisesRegex(OSError, "readback"):
                    self.trainer.commit_abnormal_deletion(prepared)
                actual = self.client.read(address, 58)
                self.assertNotEqual(actual, original)
                self.assertNotEqual(actual, bytes(58))
                self.assertEqual(self.memory.writes, 3)
                self.assertEqual(len(attempts), 58)
                files = list(self.trainer.backup_dir.glob("*.json"))
                self.assertEqual(len(files), 1)
                backup = json.loads(files[0].read_text(encoding="utf-8"))
                self.assertEqual(backup["status"], "failed-or-unconfirmed")
                self.assertEqual(
                    backup["abnormal_context"]["plan"]["before"], original.hex()
                )
                self.lua.globals()[b"emu"][b"write8"] = self.original_write
                before = dict(self.memory.data)
                commands = len(self.commands)
                with self.assertRaisesRegex(ValueError, "未被确认"):
                    self.trainer.restore(files[0])
                with self.assertRaisesRegex(ValueError, "失效"):
                    self.trainer.commit_abnormal_deletion(prepared)
                self.assertEqual(len(self.commands), commands)
                self.assertEqual(self.memory.data, before)
                self.assertEqual(self.memory.writes, 3)

    def test_preview_identity_generation_locks_and_stale_restore_fail_without_writes(
        self,
    ):
        self.use_release(CRCS[0])
        address = self.profile["storage"]["box_addresses"][0]
        self.memory.put(address, bad_flag(record(160, 42)))
        prepared = self.trainer.prepare_abnormal_deletion("box", 0, 0)
        altered = copy.deepcopy(prepared)
        altered["rom_identity"] = "sha256:" + "0" * 64
        foreign = Trainer(self.client, self.profile, self.temp.name)
        for trainer, preview in ((self.trainer, altered), (foreign, prepared)):
            with self.assertRaisesRegex(ValueError, "失效"):
                trainer.commit_abnormal_deletion(preview)
        self.trainer.locked_boxes.add(0)
        with self.assertRaisesRegex(ValueError, "锁"):
            self.trainer.commit_abnormal_deletion(prepared)
        self.assertEqual(self.memory.writes, 0)
        self.trainer.locked_boxes.clear()
        prepared = self.trainer.prepare_abnormal_deletion("box", 0, 0)
        result = self.trainer.commit_abnormal_deletion(prepared)
        self.memory.put(address + 12, b"\x99")
        before = dict(self.memory.data)
        writes = self.memory.writes
        with self.assertRaises(OSError):
            self.trainer.restore(result["backup"])
        self.assertEqual(self.memory.data, before)
        self.assertEqual(self.memory.writes, writes)

    def test_callback_readback_finishes_before_next_frame_and_restore_is_conditional(
        self,
    ):
        for crc in CRCS:
            with self.subTest(crc=crc):
                self.use_release(crc)
                prepared = self.trainer.prepare_abnormal_deletion("party", 1)

                def next_frame(line):
                    self.commands.append(line)
                    if line.startswith(b"BATCHVERIFYCRC "):
                        self.memory.put(PARTY + 599, b"\x77")

                self.after_command = next_frame
                result = self.trainer.commit_abnormal_deletion(prepared)
                _, backup = self.assert_verified_backup(result, "party")
                self.assertEqual(backup["status"], "verified")
                self.assertEqual(self.memory.read(PARTY + 599, 1), b"\x77")
                before = dict(self.memory.data)
                writes = self.memory.writes
                with self.assertRaises(OSError):
                    self.trainer.restore(result["backup"])
                self.assertEqual(self.memory.data, before)
                self.assertEqual(self.memory.writes, writes)

    def test_bridge_without_callback_readback_refuses_before_backup_or_write(self):
        self.use_release(CRCS[0])
        address = self.profile["storage"]["box_addresses"][0]
        self.memory.put(address, bad_flag(record(160, 42)))
        prepared = self.trainer.prepare_abnormal_deletion("box", 0, 0)
        capabilities = self.client.capabilities
        self.client.capabilities = capabilities - {"BATCHVERIFY"}
        try:
            with self.assertRaisesRegex(ValueError, "读回"):
                self.trainer.commit_abnormal_deletion(prepared)
            self.assertEqual(self.memory.writes, 0)
            self.assertFalse(list(self.trainer.backup_dir.glob("*.json")))
        finally:
            self.client.capabilities = capabilities


if __name__ == "__main__":
    unittest.main()
