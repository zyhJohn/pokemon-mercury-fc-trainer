import unittest
import json
import struct
import tempfile
from pathlib import Path
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from tests.test_pokemon_data import sample


class Memory:
    capabilities = {
        "BATCH",
        "ROMCRC",
        "CRCBATCH",
        "BATCH8192",
        "BATCHVERIFY",
        "BOXBATCH",
    }

    def __init__(self):
        self.data = {}
        self.writes = 0
        self.rom_crc32 = json.loads(
            Path("rom_profile.json").read_text(encoding="utf-8")
        )["rom_crc32"]
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.put(profile["trainer"]["pointer_address"], struct.pack("<I", 0x2024588))
        for sig in profile["economy"]["signatures"]:
            self.put(sig["address"], bytes.fromhex(sig["hex"]))

    def command(self, cmd):
        if cmd == "ROMCRC":
            return self.rom_crc32.encode("ascii")
        raise ValueError(cmd)

    def put(self, address, data):
        self.data.update({address + i: b for i, b in enumerate(data)})

    def read(self, a, n):
        return bytes(self.data.get(a + i, 0) for i in range(n))

    def r8(self, a):
        return self.read(a, 1)[0]

    def r32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]

    def batch(self, patches, verify=False, large_boxes=False):
        if any(self.read(a, len(b)) != b for a, b, c in patches):
            raise IOError("stale")
        for a, b, c in patches:
            if b != c:
                self.put(a, c)
                self.writes += 1


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.mem = Memory()
        self.trainer = Trainer(self.mem, profile, self.temp.name)
        for sig in profile["signatures"]:
            self.mem.put(sig["address"], bytes.fromhex(sig["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.mem.put(PARTY_COUNT, b"\x01")
        self.mem.put(PARTY, sample().raw)

    def test_signature_mismatch_blocks(self):
        self.mem.put(0x804449C, b"\0")
        with self.assertRaises(ValueError):
            self.trainer.snapshot()
        self.assertFalse(self.trainer.verified)

    def test_full_rom_crc_mismatch_blocks_even_with_matching_signatures(self):
        self.mem.rom_crc32 = "00000000"
        with self.assertRaisesRegex(ValueError, "CRC32"):
            self.trainer.snapshot()
        self.assertEqual(self.mem.writes, 0)

    def test_v2_bridge_is_read_only(self):
        self.mem.capabilities = {"BATCH"}
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        with self.assertRaisesRegex(ValueError, "新版"):
            self.trainer.commit(snap, patches, "legacy v2")
        self.assertEqual(self.mem.writes, 0)

    def test_validation_checks_unchanged_fields_too(self):
        for offset, value, message in [
            (54, b"\xff", "PP"),
            (36, b"\0\0\0\0", "经验"),
            (34, b"\x6d\x01", "携带道具"),
        ]:
            raw = bytearray(sample().raw)
            raw[offset : offset + len(value)] = value
            self.mem.put(PARTY, raw)
            snap = self.trainer.snapshot()
            with self.assertRaisesRegex(ValueError, message):
                self.trainer.edit_pokemon(snap, 0, shiny=True)
        self.assertEqual(self.mem.writes, 0)

    def test_commit_and_backup(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True, ivs=[31] * 6)
        result = self.trainer.commit(snap, patches, "test")
        self.assertTrue(result["changed"])
        record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "verified")
        self.assertEqual(bytes.fromhex(record["patches"][0]["before"]), sample().raw)
        self.assertEqual(self.mem.writes, 1)

    def test_stale_mon_or_pointer_blocks_all_writes(self):
        for address in [PARTY + 1, SAVE_POINTER, PARTY_COUNT]:
            with self.subTest(address=address):
                self.setUp()
                snap = self.trainer.snapshot()
                patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
                self.mem.put(address, b"\x07")
                with self.assertRaises(IOError):
                    self.trainer.commit(snap, patches, "stale")
                self.assertEqual(self.mem.writes, 0)

    def test_bag_pockets_and_important_quantity(self):
        snap = self.trainer.snapshot(3)
        self.assertEqual(snap["pocket"]["capacity"], 50)
        with self.assertRaises(ValueError):
            self.trainer.edit_bag(snap, 0, 13, 5)
        patches = self.trainer.edit_bag(snap, 0, 4, 164)
        self.trainer.commit(snap, patches, "balls")
        self.assertEqual(self.mem.read(0x203C354, 4), b"\x04\0\xa4\0")
        snap = self.trainer.snapshot(2)
        patches = self.trainer.edit_bag(snap, 0, 365, "")
        self.assertEqual(patches[0][2], struct.pack("<HH", 365, 1))
        patches = self.trainer.edit_bag(
            snap, 0, "text that is not an ID", "", delete=True
        )
        self.assertEqual(patches[0][2], b"\0" * 4)
        # Quantity is hidden for key items: reselecting the same item must
        # preserve its stored value even when the internal count is zero.
        self.mem.put(0x203C228, struct.pack("<HH", 365, 0))
        snap = self.trainer.snapshot(2)
        patches = self.trainer.edit_bag(snap, 0, 365, "")
        self.assertEqual(patches[0][1], patches[0][2])
        self.assertFalse(self.trainer.commit(snap, patches, "unchanged key")["changed"])

    def test_no_changes_do_not_write_or_backup(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=False)
        result = self.trainer.commit(snap, patches, "same")
        self.assertFalse(result["changed"])
        self.assertEqual(self.mem.writes, 0)
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])

    def test_restore_exact_bytes_and_refuse_stale(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True, ivs=[31] * 6)
        result = self.trainer.commit(snap, patches, "edit")
        self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.read(PARTY, 100), sample().raw)
        writes = self.mem.writes
        with self.assertRaises(IOError):
            self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.writes, writes)

    def test_reload_wrong_rom_before_write_is_detected(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        self.mem.put(0x804449C, b"\0")
        with self.assertRaises(ValueError):
            self.trainer.commit(snap, patches, "wrong ROM")
        self.assertEqual(self.mem.writes, 0)

    def test_battle_snapshot_and_race_block_party_writes(self):
        address = self.trainer.profile["battle_flag"]["address"]
        self.mem.put(address, b"\x02")
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        with self.assertRaisesRegex(ValueError, "战斗中"):
            self.trainer.commit(snap, patches, "battle")
        self.mem.put(address, b"\0")
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        self.mem.put(address, b"\x02")
        with self.assertRaises(IOError):
            self.trainer.commit(snap, patches, "battle started")
        self.assertEqual(self.mem.writes, 0)

    def test_backup_failure_prevents_write(self):
        path = Path(self.temp.name) / "not-a-directory"
        path.write_text("occupied")
        self.trainer.backup_dir = path
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        with self.assertRaises(OSError):
            self.trainer.commit(snap, patches, "backup fails")
        self.assertEqual(self.mem.writes, 0)

    def test_readback_failure_is_recorded_without_retry(self):
        snap = self.trainer.snapshot()
        patches, _ = self.trainer.edit_pokemon(snap, 0, shiny=True)
        original = self.mem.batch

        def altered(patches):
            original(patches)
            self.mem.put(PARTY + 86, b"\0")

        self.mem.batch = altered
        with self.assertRaisesRegex(IOError, "读回不一致"):
            self.trainer.commit(snap, patches, "readback mismatch")
        self.assertEqual(self.mem.writes, 1)
        record = json.loads(
            next(Path(self.temp.name).glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(record["status"], "failed-or-unconfirmed")
        self.assertEqual(bytes.fromhex(record["patches"][0]["before"]), sample().raw)


if __name__ == "__main__":
    unittest.main()
