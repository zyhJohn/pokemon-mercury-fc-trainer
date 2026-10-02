import json
import struct
import tempfile
import unittest
from pathlib import Path
from pokemon_data import Pokemon, change_shiny_pid
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from tests.test_core import Memory
from tests.test_pokemon_data import sample


class DetailTests(unittest.TestCase):
    def test_detail_bitfields_and_unrelated_bytes(self):
        original = sample()
        updated, _ = original.edit(
            friendship=123, met_location=222, met_level=99, ball=26, ot_gender=1
        )
        self.assertEqual(
            (
                updated.friendship,
                updated.met_location,
                updated.met_level,
                updated.ball,
                updated.ot_gender,
            ),
            (123, 222, 99, 26, 1),
        )
        allowed = {41, 42, 69, 70, 71}
        self.assertTrue(
            all(
                original.raw[i] == updated.raw[i]
                for i in range(100)
                if i not in allowed
            )
        )
        self.assertEqual(original.raw[70] & 128, updated.raw[70] & 128)
        self.assertEqual(original.raw[71] & 127, updated.raw[71] & 127)

    def test_ot_ids_preserve_shiny_nature_gender_and_ability(self):
        for shiny in [False, True]:
            raw = bytearray(sample().raw)
            mon = Pokemon(bytes(raw))
            struct.pack_into(
                "<I", raw, 0, change_shiny_pid(mon.pid, mon.otid, shiny, mon.species)
            )
            mon = Pokemon(bytes(raw))
            for tid, sid in [(0, 0), (65535, 65535), (12345, 54321)]:
                updated, _ = mon.edit(ot_tid=tid, ot_sid=sid)
                self.assertEqual(updated.otid, tid | sid << 16)
                self.assertEqual(updated.shiny, shiny)
                self.assertEqual(updated.pid % 25, mon.pid % 25)
                self.assertEqual(updated.pid & 255, mon.pid & 255)
                self.assertEqual(updated.raw[8:], mon.raw[8:])

    def test_detail_ranges(self):
        for change in [
            dict(friendship=256),
            dict(met_location=-1),
            dict(met_level=128),
            dict(ball=256),
            dict(ot_gender=2),
            dict(ot_tid=65536),
            dict(ot_sid=-1),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                sample().edit(**change)

    def test_egg_conversion_synchronizes_flags_level_cycles_and_iv_flags(self):
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        mon = sample()
        metadata = profile["species"][str(mon.species)]
        kwargs = dict(
            base=metadata["base"],
            growth=metadata["growth"],
            experience_tables=profile["experience_tables"],
            egg_cycles=metadata["egg_cycles"],
            default_friendship=metadata["friendship"],
        )
        updated, _ = mon.edit(egg=True, ivs=[31] * 6, **kwargs)
        self.assertTrue(updated.egg)
        self.assertTrue(updated.raw[19] & 4)
        self.assertEqual(updated.level, 1)
        self.assertEqual(updated.experience, 1)
        self.assertEqual(updated.friendship, metadata["egg_cycles"])
        self.assertEqual(updated.met_level, 0)
        self.assertEqual(updated.ability_flag, mon.ability_flag)
        self.assertEqual(updated.raw[8:19], mon.raw[8:19])
        normal, _ = updated.edit(egg=False, **kwargs)
        self.assertFalse(normal.egg)
        self.assertFalse(normal.raw[19] & 4)
        self.assertEqual(normal.friendship, metadata["friendship"])
        self.assertEqual(normal.ivs, (31,) * 6)


class TrainerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.mem = Memory()
        self.trainer = Trainer(self.mem, profile, self.temp.name)
        for sig in profile["signatures"]:
            self.mem.put(sig["address"], bytes.fromhex(sig["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x202552C))
        self.mem.put(PARTY_COUNT, b"\1")
        self.mem.put(PARTY, sample().raw)
        self.pointer = profile["trainer"]["pointer_address"]
        self.address = 0x2024588
        self.mem.put(self.pointer, struct.pack("<I", self.address))
        self.raw = (
            b"\xee\xed\xdc" + b"\xff" * 5 + b"\0\0" + struct.pack("<HH", 12345, 54321)
        )
        self.mem.put(self.address, self.raw)

    def test_player_id_write_and_restore_keep_name_party_and_gender(self):
        snap = self.trainer.snapshot_trainer()
        before_party = self.mem.read(PARTY, 100)
        result = self.trainer.commit_trainer_ids(snap, 65535, 0)
        self.assertEqual(self.mem.read(self.address, 10), self.raw[:10])
        self.assertEqual(self.mem.read(PARTY, 100), before_party)
        self.assertEqual(self.trainer.snapshot_trainer()["tid"], 65535)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.read(self.address, 14), self.raw)

    def test_player_pointer_race_refuses_without_writing(self):
        snap = self.trainer.snapshot_trainer()
        self.mem.put(self.pointer, struct.pack("<I", self.address + 0x100))
        with self.assertRaises(IOError):
            self.trainer.commit_trainer_ids(snap, 1, 2)
        self.assertEqual(self.mem.writes, 0)

    def test_stale_player_ids_refused(self):
        snap = self.trainer.snapshot_trainer()
        self.mem.put(self.address + 10, b"\0" * 4)
        with self.assertRaises(IOError):
            self.trainer.commit_trainer_ids(snap, 1, 2)
        self.assertEqual(self.mem.writes, 0)

    def test_player_ids_refused_in_battle(self):
        snap = self.trainer.snapshot_trainer()
        self.mem.put(self.trainer.profile["battle_flag"]["address"], b"\2")
        with self.assertRaisesRegex(ValueError, "战斗中"):
            self.trainer.commit_trainer_ids(snap, 1, 2)
        self.assertEqual(self.mem.writes, 0)
