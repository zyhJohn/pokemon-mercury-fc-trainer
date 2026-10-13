"""New release selection, metadata differences and cross-ROM write protection."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from distribution_catalog import load_distributions, usable_rows
from game_fields import box_name_pointer_table
from rom_versions import load_profile
from sidequest_data import read_snapshot
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


class V133Tests(unittest.TestCase):
    def test_new_metadata_is_independent_and_existing_layout_remains_supported(self):
        previous = load_profile(crc="4755f497")
        profile = load_profile(crc="518482f7")
        self.assertEqual(profile["species"]["916"]["abilities"], [129, 129, 129])
        self.assertEqual(previous["species"]["916"]["abilities"], [39, 0, 0])
        self.assertEqual(profile["items"]["57"]["price"], 200)
        self.assertEqual(previous["items"]["57"]["price"], 3000)
        self.assertEqual(profile["pockets"], previous["pockets"])
        self.assertEqual(profile["storage"]["box_addresses"], previous["storage"]["box_addresses"])
        self.assertEqual(box_name_pointer_table(profile["rom_sha256"]), 0x09DE4AE0)
        self.assertEqual(len(usable_rows(load_distributions(), profile)), 6)

    def test_stale_code_guard_blocks_before_write_and_foreign_backup_is_refused(self):
        profile = load_profile(crc="518482f7")
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        with tempfile.TemporaryDirectory() as folder:
            trainer = Trainer(memory, profile, folder)
            snapshot = trainer.snapshot()
            patches = trainer.edit_values(snapshot, 100, 735, 5, 220)
            signature = next(s for s in profile["signatures"] if s["address"] == 0x09DE3A94)
            guard = bytes.fromhex(signature["hex"])
            memory.put(signature["address"], bytes((guard[0] ^ 1,)) + guard[1:])
            with self.assertRaises((ValueError, OSError)):
                trainer.commit(snapshot, patches, "tampered guard")
            self.assertEqual(memory.writes, 0)
            memory.put(signature["address"], guard)
            result = trainer.commit(snapshot, patches, "V1.33 economy")
            previous = load_profile(crc="4755f497")
            install_profile(memory, previous)
            writes = memory.writes
            with self.assertRaises(ValueError):
                Trainer(memory, previous, folder).restore(result["backup"])
            self.assertEqual(memory.writes, writes)

    def test_new_sidequest_release_keeps_extra_acceptance_and_unread_semantics(self):
        from tests.test_sidequests import Memory as QuestMemory

        profile = load_profile(crc="518482f7")
        extra_flag = 0x1044
        address = 0x0203B174 + (extra_flag - 0x900) // 8
        memory = QuestMemory({address: 1 << (extra_flag & 7)})
        statuses = read_snapshot(memory, profile)
        self.assertEqual(len(statuses), 96)
        self.assertEqual(statuses["001"], "in_progress")
        self.assertEqual(set(read_snapshot(QuestMemory({}, fail=True), profile).values()), {"unknown"})

    def test_tampered_new_profile_identity_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "rom_profile_v133.json").write_text(
                json.dumps({"rom_sha256": "wrong", "rom_crc32": "518482f7"}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_profile(crc="518482f7", root=folder)
