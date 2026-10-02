import json
import struct
import tempfile
import unittest
from pathlib import Path
from box_data import BoxPokemon
from trainer_core import Trainer, PARTY_COUNT, SAVE_POINTER
from tests.test_core import Memory


def packed_box():
    raw = bytearray(58)
    struct.pack_into("<II", raw, 0, 0x12345678, 0x9ABCDEF0)
    raw[19] = 2
    struct.pack_into("<HHI", raw, 28, 160, 0, 117360)
    raw[39:44] = sum(m << (i * 10) for i, m in enumerate([1023, 1, 700, 0])).to_bytes(
        5, "little"
    )
    raw[44:50] = bytes([252, 0, 0, 252, 0, 6])
    struct.pack_into(
        "<I",
        raw,
        54,
        0x80000000 | sum(v << (i * 5) for i, v in enumerate([31, 0, 1, 15, 30, 16])),
    )
    return bytes(raw)


class BoxTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.memory = Memory()
        for s in self.profile["signatures"] + self.profile["storage"]["signatures"]:
            self.memory.put(s["address"], bytes.fromhex(s["hex"]))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trainer = Trainer(self.memory, self.profile, self.temp.name)

    def test_packed_moves_and_iv_flags_do_not_bleed_between_fields(self):
        mon = BoxPokemon(packed_box())
        self.assertEqual(mon.moves, (1023, 1, 700, 0))
        self.assertEqual(mon.ivs, (31, 0, 1, 15, 30, 16))
        self.assertEqual(mon.ability_flag, 1)
        self.assertFalse(mon.egg)
        self.assertEqual(mon.evs, (252, 0, 0, 252, 0, 6))
        self.assertEqual(mon.describe(self.profile)["level"], 50)

    def test_reads_noncontiguous_last_box_and_does_not_write(self):
        layout = self.profile["storage"]
        self.memory.put(layout["box_addresses"][24] + 29 * 58, packed_box())
        snap = self.trainer.snapshot_box(24)
        self.assertEqual(snap["pokemon"][29].species, 160)
        self.assertEqual(snap["pokemon"][0].species, 0)
        self.assertEqual(self.memory.writes, 0)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_bad_rom_signature_and_changed_snapshot_refused(self):
        s = self.profile["storage"]["signatures"][0]
        self.memory.put(s["address"], bytes([bytes.fromhex(s["hex"])[0] ^ 255]))
        with self.assertRaisesRegex(ValueError, "PC 压缩结构"):
            self.trainer.snapshot_box(0)
        self.memory.put(s["address"], bytes.fromhex(s["hex"]))
        original = self.memory.read
        address = self.profile["storage"]["box_addresses"][0]
        reads = 0

        def read(a, n):
            nonlocal reads
            value = original(a, n)
            if a == address:
                reads += 1
                if reads == 2:
                    return b"\1" + value[1:]
            return value

        self.memory.read = read
        with self.assertRaisesRegex(ValueError, "盒子内容已变化"):
            self.trainer.snapshot_box(0)

    def test_invalid_length_and_zero_experience_report(self):
        with self.assertRaises(ValueError):
            BoxPokemon(b"\0" * 80)
        raw = bytearray(packed_box())
        raw[32:36] = b"\0" * 4
        result = BoxPokemon(bytes(raw)).describe(self.profile)
        self.assertEqual(result["level"], 0)
        self.assertIn("经验低于 1 级门槛", result["errors"])

    def test_edit_preserves_packed_metadata_and_round_trip_restore(self):
        raw = bytearray(packed_box())
        raw[39:44] = sum(
            m << (i * 10) for i, m in enumerate([757, 242, 8, 700])
        ).to_bytes(5, "little")
        raw[8:19] = bytes(range(11))
        raw[19] |= 0xF8
        raw[50:54] = b"\x92\x35\x41\x67"
        self.memory.put(SAVE_POINTER, struct.pack("<I", 0x202552C))
        self.memory.put(PARTY_COUNT, b"\0")
        address = self.profile["storage"]["box_addresses"][24] + 29 * 58
        self.memory.put(address, raw)
        snap = self.trainer.snapshot_box(24)
        patches, report = self.trainer.edit_box(
            snap, 29, shiny=True, nature=3, ivs=[31] * 6, evs=[0, 252, 0, 252, 0, 6]
        )
        changed = BoxPokemon(patches[0][2])
        self.assertTrue(changed.shiny)
        self.assertEqual(changed.pid % 25, 3)
        self.assertEqual(changed.ivs, (31,) * 6)
        self.assertEqual(changed.ability_flag, 1)
        allowed = set(range(4)) | set(range(44, 50)) | set(range(54, 58))
        for i in range(58):
            if i not in allowed:
                self.assertEqual(raw[i], changed.raw[i])
        result = self.trainer.commit_box(patches, "box test")
        self.assertEqual(self.memory.read(address, 58), changed.raw)
        self.trainer.restore(result["backup"])
        self.assertEqual(self.memory.read(address, 58), bytes(raw))

    def test_edit_empty_and_invalid_evs_refused(self):
        with self.assertRaises(ValueError):
            BoxPokemon(b"\0" * 58).edit(self.profile, shiny=True)
        with self.assertRaises(ValueError):
            BoxPokemon(packed_box()).edit(self.profile, evs=[252] * 6)

    def test_shiny_edit_checks_existing_moves_and_held_item(self):
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        # An unrelated shiny/IV edit must not declare invalid held items valid.
        for held in [365, 341, 65535]:
            with self.subTest(held=held):
                struct.pack_into("<H", raw, 30, held)
                with self.assertRaisesRegex(ValueError, "携带道具"):
                    BoxPokemon(bytes(raw)).edit(self.profile, shiny=True)
        struct.pack_into("<H", raw, 30, 0)
        for moves, error in [([757, 757], "重复招式"), ([], "至少需要一个招式")]:
            with self.subTest(moves=moves):
                raw[39:44] = sum(m << (i * 10) for i, m in enumerate(moves)).to_bytes(
                    5, "little"
                )
                with self.assertRaisesRegex(ValueError, error):
                    BoxPokemon(bytes(raw)).edit(self.profile, ivs=[31] * 6)


if __name__ == "__main__":
    unittest.main()
