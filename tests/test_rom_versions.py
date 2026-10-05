import json
import struct
import tempfile
import unittest
from pathlib import Path

from rom_versions import RELEASES, V12_ADDRESSES, load_profile, release
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_time import put_time


def install_profile(memory, profile):
    memory.rom_crc32 = profile["rom_crc32"]
    for signatures in [
        profile["signatures"],
        *[profile[g]["signatures"] for g in ("storage", "economy", "time")],
    ]:
        for sig in signatures:
            memory.put(sig["address"], bytes.fromhex(sig["hex"]))


class ReleaseTests(unittest.TestCase):
    def test_exact_release_selection_and_unknown_rejection(self):
        for sha, (version, crc, _) in RELEASES.items():
            profile = load_profile(crc=crc)
            self.assertEqual(profile["rom_sha256"], sha)
            self.assertIn(version, profile["name"])
        with self.assertRaises(ValueError):
            load_profile(crc="00000000")
        with self.assertRaises(ValueError):
            release(b"BPRE superficially similar ROM")

    def test_inconsistent_packaged_identity_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "rom_profile_v12.json").write_text(
                json.dumps({"rom_sha256": "wrong", "rom_crc32": "4755f497"})
            )
            with self.assertRaises(ValueError):
                load_profile(crc="4755f497", root=folder)

    def test_version_specific_tables_and_ram_layout(self):
        old, new = load_profile(crc="b4af11c8"), load_profile(crc="4755f497")
        self.assertNotEqual(old["moves_address"], new["moves_address"])
        self.assertEqual(old["pockets"], new["pockets"])
        self.assertEqual(
            old["storage"]["box_addresses"], new["storage"]["box_addresses"]
        )
        self.assertEqual(new["species"]["528"]["abilities"][1], 129)
        self.assertEqual(old["species"]["528"]["abilities"][1], 118)
        self.assertEqual(len(old["signatures"]), len(new["signatures"]))
        for old_address, new_address in V12_ADDRESSES.items():
            self.assertTrue(0x08000000 <= old_address < 0x0A000000)
            self.assertTrue(0x08000000 <= new_address < 0x0A000000)

    def test_cross_version_crc_and_backup_refused(self):
        old, new = load_profile(crc="b4af11c8"), load_profile(crc="4755f497")
        memory = Memory()
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        with tempfile.TemporaryDirectory() as folder:
            install_profile(memory, old)
            trainer = Trainer(memory, old, folder)
            snap = trainer.snapshot()
            result = trainer.commit(
                snap, trainer.edit_values(snap, 100, 735, 5, 220), "old release"
            )
            install_profile(memory, new)
            before = memory.writes
            with self.assertRaises(ValueError):
                trainer.snapshot()
            with self.assertRaises(ValueError):
                Trainer(memory, new, folder).restore(result["backup"])
            self.assertEqual(memory.writes, before)

    def test_v12_time_virtual_marker_and_restore(self):
        profile = load_profile(crc="4755f497")
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        put_time(memory, profile)
        memory.put(profile["time"]["virtual_clock"]["marker_address"], b"\x56")
        with tempfile.TemporaryDirectory() as folder:
            trainer = Trainer(memory, profile, folder)
            time = trainer.snapshot_time()
            self.assertTrue(time["virtual_clock"])
            result = trainer.commit_playtime(time, 12, 34, 56)
            self.assertEqual(trainer.snapshot_time()["playtime"][:3], (12, 34, 56))
            trainer.restore(result["backup"])
            self.assertEqual(trainer.snapshot_time()["playtime"][:3], (26, 4, 18))
