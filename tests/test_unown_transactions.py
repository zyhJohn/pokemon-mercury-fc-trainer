"""Unown record edits use the existing backup and atomic Lua transaction path."""

from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from rom_versions import load_profile
from tests import test_end_to_end, test_rom_versions
from tests.test_bridge import LuaRuntime
from tests.test_core import Memory
from trainer_core import PARTY_COUNT, SAVE_POINTER, Trainer
from unown_records import (
    CAPTURE_ADDRESS, FINAL_ADDRESS, RESEARCH_ADDRESS, unown_rom_features,
)


REWARD_SCRIPT_START = 0x08EC83C6
REWARD_SCRIPT_END = 0x08EC8642


def reward_script_feature(profile):
    return next(
        (address, raw) for address, raw in unown_rom_features(profile["rom_sha256"])
        if address <= REWARD_SCRIPT_START and address + len(raw) >= REWARD_SCRIPT_END
    )


def seed(memory, profile):
    test_rom_versions.install_profile(memory, profile)
    memory.put(SAVE_POINTER, struct.pack("<II", 0x0202552C, 0x02024588))
    memory.put(PARTY_COUNT, b"\0")
    memory.put(CAPTURE_ADDRESS, bytes.fromhex("030000c0"))
    memory.put(RESEARCH_ADDRESS, b"\xc0")
    memory.put(FINAL_ADDRESS, b"\xa2")
    for address, raw in unown_rom_features(profile["rom_sha256"]):
        memory.put(address, raw)


def all_changes(snapshot):
    return {field["key"]: True for field in snapshot["fields"] if field["editable"]}


class UnownTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.profile = load_profile(crc="4755f497")
        self.memory = Memory()
        seed(self.memory, self.profile)
        self.trainer = Trainer(self.memory, self.profile, self.folder)

    def preview(self, changes=None):
        snapshot = self.trainer.snapshot_unown_records()
        return self.trainer.prepare_unown_records(snapshot, all_changes(snapshot) if changes is None else changes)

    def test_two_records_one_backup_boundary_bits_and_restore_three_releases(self):
        for crc in ("b4af11c8", "4755f497", "518482f7"):
            with self.subTest(crc=crc):
                self.profile = load_profile(crc=crc)
                seed(self.memory, self.profile)
                self.trainer = Trainer(self.memory, self.profile, self.folder)
                prepared = self.preview()
                before = dict(prepared["snapshot"]["records"])
                calls = []
                batch = self.memory.batch
                with patch.object(self.memory, "batch", side_effect=lambda patches, **kwargs: (
                    self.assertTrue(kwargs["verify"]), calls.append(patches), batch(patches, **kwargs)
                )[-1]):
                    result = self.trainer.commit_unown_records(prepared)
                self.assertEqual(len(calls), 1)
                self.assertLessEqual(len(calls[0]), 64)
                self.assertLessEqual(sum(len(before) for _, before, _ in calls[0]), 4096)
                script_address, script = reward_script_feature(self.profile)
                self.assertIn((script_address, script, script), calls[0])
                self.assertEqual(sum(p[0] == SAVE_POINTER for p in calls[0]), 1)
                self.assertEqual(sum(p[0] == SAVE_POINTER + 4 for p in calls[0]), 1)
                self.assertTrue(all(len(p[1]) == 4 for p in calls[0] if p[0] in (SAVE_POINTER, SAVE_POINTER + 4)))
                self.assertEqual(self.memory.read(CAPTURE_ADDRESS, 4), b"\xff" * 4)
                self.assertEqual(self.memory.read(RESEARCH_ADDRESS, 1), b"\xff")
                self.assertEqual(self.memory.read(FINAL_ADDRESS, 1), b"\xa2")
                backup = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
                self.assertEqual(backup["status"], "verified")
                self.assertEqual(len(backup["patches"]), 2)
                self.assertIn("unown_context", backup)
                self.trainer.restore(result["backup"])
                self.assertEqual({a: self.memory.read(a, len(raw)) for a, raw in before.items()}, before)

    def test_unknown_readonly_nonbool_and_tampered_snapshot_previews_reject(self):
        original = self.trainer.snapshot_unown_records()
        for changes in ({"note_final": True}, {"task_accepted": True}, {"captured_A": 1}, {"other": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.trainer.prepare_unown_records(original, changes)
        for key in ("guards", "records", "fields", "snapshot_nonce", "rom_identity"):
            altered = deepcopy(original)
            altered[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.trainer.prepare_unown_records(altered, {"captured_A": True})
        preview = self.trainer.prepare_unown_records(original, {"captured_A": True})
        for key, value in (("changes", {"captured_B": True}), ("patches", ()), ("preview_nonce", "wrong")):
            changed = deepcopy(preview)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.trainer.commit_unown_records(changed)
        self.assertEqual(self.memory.writes, 0)

    def test_preview_old_pointer_records_final_battle_crc_and_rom_features_zero_writes(self):
        for target, value in (
            (SAVE_POINTER, struct.pack("<I", 0x02025530)),
            (SAVE_POINTER + 4, struct.pack("<I", 0x0202458C)),
            (CAPTURE_ADDRESS, b"\x07"),
            (RESEARCH_ADDRESS, b"\xc1"),
            (FINAL_ADDRESS, b"\xa6"),
            (self.profile["battle_flag"]["address"], b"\x02"),
            (unown_rom_features(self.profile["rom_sha256"])[0][0], b"\0"),
            (None, None),
        ):
            with self.subTest(target=target):
                seed(self.memory, self.profile)
                self.memory.put(self.profile["battle_flag"]["address"], b"\0")
                self.trainer = Trainer(self.memory, self.profile, self.folder)
                prepared = self.preview()
                if target is None:
                    self.memory.rom_crc32 = "00000000"
                else:
                    self.memory.put(target, value)
                before = dict(self.memory.data)
                with self.assertRaises((ValueError, OSError)):
                    self.trainer.commit_unown_records(prepared)
                self.assertEqual(self.memory.data, before)
                self.assertEqual(self.memory.writes, 0)

    def test_unchanged_record_is_guarded_and_battle_snapshot_cannot_write(self):
        prepared = self.preview({"captured_A": True})
        self.memory.put(RESEARCH_ADDRESS, b"\xc1")
        with self.assertRaises(OSError):
            self.trainer.commit_unown_records(prepared)
        self.assertEqual(self.memory.writes, 0)
        self.memory.put(self.profile["battle_flag"]["address"], bytes([self.profile["battle_flag"]["mask"]]))
        prepared = self.preview()
        self.memory.put(self.profile["battle_flag"]["address"], b"\0")
        with self.assertRaisesRegex(ValueError, "战斗"):
            self.trainer.commit_unown_records(prepared)

    def test_failed_readback_preserves_unconfirmed_backup_and_refuses_restore(self):
        prepared = self.preview()
        batch = self.memory.batch

        def corrupt(patches, **kwargs):
            self.assertTrue(kwargs["verify"])
            batch(patches, **kwargs)
            self.memory.put(CAPTURE_ADDRESS, b"\x03")
            raise IOError("fixture readback failed inside callback")

        with patch.object(self.memory, "batch", side_effect=corrupt), self.assertRaises(OSError):
            self.trainer.commit_unown_records(prepared)
        files = list(self.folder.glob("*.json"))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed-or-unconfirmed")
        self.assertEqual(record["unown_context"]["before"][str(CAPTURE_ADDRESS)], "030000c0")
        with self.assertRaisesRegex(ValueError, "未被确认"):
            self.trainer.restore(files[0])

    def test_restore_refuses_changed_neighbours_final_pointer_or_gameplay_records(self):
        result = self.trainer.commit_unown_records(self.preview({"captured_A": True}))
        original = dict(self.memory.data)
        writes = self.memory.writes
        for target, raw in (
            (CAPTURE_ADDRESS, b"\x06"),  # boundary neighbour differs
            (CAPTURE_ADDRESS + 1, b"\x01"),  # gameplay capture changed
            (RESEARCH_ADDRESS, b"\xc1"),
            (FINAL_ADDRESS, b"\xa6"),
            (SAVE_POINTER + 4, struct.pack("<I", 0x0202458C)),
        ):
            with self.subTest(target=target):
                self.memory.data = dict(original)
                self.memory.put(target, raw)
                with self.assertRaises(ValueError):
                    self.trainer.restore(result["backup"])
                self.assertEqual(self.memory.writes, writes)

    def test_restore_rejects_legacy_forged_addresses_masks_final_and_context(self):
        result = self.trainer.commit_unown_records(self.preview())
        record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        writes = self.memory.writes
        path = self.folder / "altered.json"
        variants = []
        old = deepcopy(record)
        del old["unown_context"]
        old["label"] = "未知图腾捕获与研究记录"
        variants.append(old)
        for address, old_hex, new_hex in (
            (CAPTURE_ADDRESS, "020000c0", "ffffffff"),
            (RESEARCH_ADDRESS, "80", "ff"),
            (FINAL_ADDRESS, "a2", "a6"),
            (CAPTURE_ADDRESS + 1, "00", "01"),
            (0x0203B330, "00", "01"),
        ):
            altered = deepcopy(record)
            altered["patches"] = [{"address": address, "before": old_hex, "after": new_hex}]
            variants.append(altered)
        for address, original in ((CAPTURE_ADDRESS, "020000c0"), (RESEARCH_ADDRESS, "80")):
            altered = deepcopy(record)
            altered["unown_context"]["before"][str(address)] = original
            next(item for item in altered["patches"] if item["address"] == address)["before"] = original
            variants.append(altered)
        for altered in variants:
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.subTest(patches=altered["patches"]), self.assertRaises(ValueError):
                self.trainer.restore(path)
        self.assertEqual(self.memory.writes, writes)

    def test_actual_rom_generation_nonce_and_replay_are_bound(self):
        prepared = self.preview()
        actual = {**self.profile, "rom_identity": "crc32:11111111"}
        self.trainer.profile = actual
        with self.assertRaisesRegex(ValueError, "另一ROM"):
            self.trainer.commit_unown_records(prepared)
        self.trainer.profile = self.profile
        another = Trainer(self.memory, self.profile, self.folder)
        with self.assertRaisesRegex(ValueError, "旧连接"):
            another.commit_unown_records(prepared)
        result = self.trainer.commit_unown_records(prepared)
        with self.assertRaises(ValueError):
            self.trainer.commit_unown_records(prepared)
        self.trainer.profile = actual
        with self.assertRaisesRegex(ValueError, "ROM"):
            self.trainer.restore(result["backup"])

    def test_two_compatible_actual_roms_cannot_exchange_preview_or_backup(self):
        baseline = self.profile
        profile_a = {**baseline, "compatibility": {"fixture": True},
                     "rom_crc32": "11111111", "rom_identity": "crc32:11111111"}
        profile_b = {**baseline, "compatibility": {"fixture": True},
                     "rom_crc32": "22222222", "rom_identity": "crc32:22222222"}
        self.trainer.profile, self.memory.rom_crc32 = profile_a, profile_a["rom_crc32"]
        with patch("trainer_core.runtime_compatibility_check"):
            prepared = self.preview()
            self.trainer.profile, self.memory.rom_crc32 = profile_b, profile_b["rom_crc32"]
            with self.assertRaisesRegex(ValueError, "另一ROM"):
                self.trainer.commit_unown_records(prepared)
            self.assertEqual(self.memory.writes, 0)
            self.trainer.profile, self.memory.rom_crc32 = profile_a, profile_a["rom_crc32"]
            result = self.trainer.commit_unown_records(prepared)
            writes = self.memory.writes
            self.trainer.profile, self.memory.rom_crc32 = profile_b, profile_b["rom_crc32"]
            with self.assertRaisesRegex(ValueError, "ROM"):
                self.trainer.restore(result["backup"])
            self.assertEqual(self.memory.writes, writes)

    def test_full_reward_script_mutation_before_read_or_after_preview_rejects_known_and_compatible_rom(self):
        for crc in ("b4af11c8", "4755f497", "518482f7"):
            for compatible in (False, True):
                for stage in ("before_read", "after_preview"):
                    with self.subTest(crc=crc, compatible=compatible, stage=stage):
                        profile = load_profile(crc=crc)
                        if compatible:
                            profile = {**profile, "compatibility": {"fixture": True},
                                       "rom_identity": "crc32:11111111", "rom_crc32": "11111111"}
                        seed(self.memory, profile)
                        self.trainer = Trainer(self.memory, profile, self.folder)
                        address, raw = reward_script_feature(profile)
                        self.assertEqual((address, len(raw)), (REWARD_SCRIPT_START, REWARD_SCRIPT_END - REWARD_SCRIPT_START))
                        with patch("trainer_core.runtime_compatibility_check"):
                            prepared = self.preview() if stage == "after_preview" else None
                            tail = REWARD_SCRIPT_END - 1
                            original = self.memory.read(tail, 1)
                            self.memory.put(tail, bytes([original[0] ^ 1]))
                            before = dict(self.memory.data)
                            if stage == "before_read":
                                with self.assertRaisesRegex(ValueError, "ROM"):
                                    self.trainer.snapshot_unown_records()
                            else:
                                with self.assertRaises(OSError):
                                    self.trainer.commit_unown_records(prepared)
                            self.assertEqual(self.memory.data, before)
                            self.assertEqual(self.memory.writes, 0)


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class UnownLuaTransactionTests(unittest.TestCase):
    def setUp(self):
        test_end_to_end.EndToEndTests.setUp(self)
        seed(self.memory, self.profile)

    def test_real_lua_tcp_two_records_atomically_write_restore_and_guard_capacity(self):
        sent = []
        self.after_command = lambda line: sent.append(line) if line.startswith(b"BATCHVERIFYCRC ") else None
        snapshot = self.trainer.snapshot_unown_records()
        prepared = self.trainer.prepare_unown_records(snapshot, all_changes(snapshot))
        result = self.trainer.commit_unown_records(prepared)
        self.assertEqual(len(sent), 1)
        parts = sent[0].split(b" ", 2)[2].split(b";")
        self.assertLessEqual(len(parts), 64)
        self.assertLessEqual(sum(len(p.split(b":")[1]) // 2 for p in parts), 4096)
        script_address, script = reward_script_feature(self.profile)
        self.assertIn(f"{script_address:x}:{script.hex()}:{script.hex()}".encode(), parts)
        self.assertEqual(sum(p.split(b":")[1] != p.split(b":")[2] for p in parts), 2)
        self.assertEqual(self.memory.read(CAPTURE_ADDRESS, 4), b"\xff" * 4)
        self.assertEqual(self.memory.read(RESEARCH_ADDRESS, 1), b"\xff")
        self.trainer.restore(result["backup"])
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(line.startswith(b"BATCHVERIFYCRC ") for line in sent))
        self.assertEqual(self.memory.read(CAPTURE_ADDRESS, 4), bytes.fromhex("030000c0"))
        self.assertEqual(self.memory.read(RESEARCH_ADDRESS, 1), b"\xc0")
        self.assertEqual(self.memory.read(FINAL_ADDRESS, 1), b"\xa2")

    def test_lua_callback_detects_pointer_value_final_battle_crc_races_before_any_write(self):
        original_batch = self.client.batch
        for target, raw in (
            (SAVE_POINTER + 4, struct.pack("<I", 0x0202458C)),
            (CAPTURE_ADDRESS, b"\x07"),
            (RESEARCH_ADDRESS, b"\xc1"),
            (FINAL_ADDRESS, b"\xa6"),
            (self.profile["battle_flag"]["address"], bytes([self.profile["battle_flag"]["mask"]])),
            (None, None),
        ):
            with self.subTest(target=target):
                seed(self.memory, self.profile)
                self.memory.put(self.profile["battle_flag"]["address"], b"\0")
                snapshot = self.trainer.snapshot_unown_records()
                prepared = self.trainer.prepare_unown_records(snapshot, {"captured_A": True})

                def race(patches, **kwargs):
                    if target is None:
                        self.memory.rom_crc32 = "00000000"
                    else:
                        self.memory.put(target, raw)
                    return original_batch(patches, **kwargs)

                with patch.object(self.client, "batch", side_effect=race), self.assertRaises(OSError):
                    self.trainer.commit_unown_records(prepared)
                self.assertEqual(self.memory.writes, 0)

    def test_lua_exact_64_guard_capacity_and_65_rejected_before_backup(self):
        snapshot = self.trainer.snapshot_unown_records()
        prepared = self.trainer.prepare_unown_records(snapshot, {"captured_A": True})
        patches = list(prepared["patches"])
        base_guards, _ = self.trainer._unown_transaction_context(snapshot, patches)
        base_guards += [(SAVE_POINTER, snapshot["guards"][0][1][:4], snapshot["guards"][0][1][:4]),
                        (PARTY_COUNT, b"\0", b"\0"),
                        (self.profile["battle_flag"]["address"], b"\0", b"\0")]
        base_guards += [(s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
                        for s in self.profile["signatures"]]
        base_count = len(self.trainer._merge_unown_guards(base_guards, patches)) + len(patches)
        snap = self.trainer.snapshot()
        snap["unown_records"] = snapshot
        snap["box_name_guards"] = [(0x02001000 + i, b"\0", b"\0") for i in range(64 - base_count + 1)]
        with self.assertRaisesRegex(ValueError, "64"):
            self.trainer.commit(snap, patches, "capacity test")
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])
        snap["box_name_guards"].pop()
        self.trainer.commit(snap, patches, "capacity test")
        self.assertEqual(self.memory.read(CAPTURE_ADDRESS, 1), b"\x07")

    def test_lua_failed_write_readback_keeps_full_unconfirmed_record(self):
        snapshot = self.trainer.snapshot_unown_records()
        prepared = self.trainer.prepare_unown_records(snapshot, all_changes(snapshot))
        self.lua.globals()[b"emu"][b"write8"] = lambda *_: None
        responses = []
        handle = self.handle

        def capture(line):
            response = handle(line)
            if line.startswith(b"BATCHVERIFYCRC "):
                responses.append(response)
            return response

        self.handle = capture
        with patch.object(self.client, "batch", wraps=self.client.batch) as batch:
            with self.assertRaises(OSError):
                self.trainer.commit_unown_records(prepared)
        self.assertTrue(batch.call_args.kwargs["verify"])
        self.assertEqual(responses, [b"ERR readback"])
        records = list(Path(self.temp.name).glob("*.json"))
        self.assertEqual(len(records), 1)
        backup = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(backup["status"], "failed-or-unconfirmed")
        self.assertEqual(len(backup["patches"]), 2)
        self.assertEqual(backup["unown_context"]["before"][str(CAPTURE_ADDRESS)], "030000c0")
        with self.assertRaisesRegex(ValueError, "未被确认"):
            self.trainer.restore(records[0])

    def test_lua_full_reward_script_before_read_after_preview_and_callback_mutations_write_nothing(self):
        batch = self.client.batch
        for crc in ("b4af11c8", "4755f497", "518482f7"):
            for stage in ("before_read", "after_preview", "inside_callback_request"):
                with self.subTest(crc=crc, stage=stage):
                    profile = load_profile(crc=crc)
                    seed(self.memory, profile)
                    self.trainer = Trainer(self.client, profile, self.temp.name)
                    address, raw = reward_script_feature(profile)
                    self.assertEqual((address, len(raw)), (REWARD_SCRIPT_START, REWARD_SCRIPT_END - REWARD_SCRIPT_START))

                    def mutate_script():
                        tail = REWARD_SCRIPT_END - 1
                        current = self.memory.read(tail, 1)
                        self.memory.put(tail, bytes([current[0] ^ 1]))

                    if stage == "before_read":
                        mutate_script()
                        with self.assertRaisesRegex(ValueError, "ROM"):
                            self.trainer.snapshot_unown_records()
                    else:
                        snapshot = self.trainer.snapshot_unown_records()
                        prepared = self.trainer.prepare_unown_records(snapshot, all_changes(snapshot))
                        if stage == "after_preview":
                            mutate_script()
                            with self.assertRaises(OSError):
                                self.trainer.commit_unown_records(prepared)
                        else:
                            def race(patches, **kwargs):
                                self.assertLessEqual(len(patches), 64)
                                self.assertLessEqual(sum(len(before) for _, before, _ in patches), 4096)
                                mutate_script()
                                return batch(patches, **kwargs)

                            with patch.object(self.client, "batch", side_effect=race), self.assertRaises(OSError):
                                self.trainer.commit_unown_records(prepared)
                    self.assertEqual(self.memory.writes, 0)
                    self.assertEqual(self.memory.read(CAPTURE_ADDRESS, 4), bytes.fromhex("030000c0"))
                    self.assertEqual(self.memory.read(RESEARCH_ADDRESS, 1), b"\xc0")
                    self.assertEqual(self.memory.read(FINAL_ADDRESS, 1), b"\xa2")


if __name__ == "__main__":
    unittest.main()
