import json
import struct
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests.test_core import Memory
from tests.test_pokemon_data import sample
from trainer_core import Trainer, SAVE_POINTER, PARTY, PARTY_COUNT


class EconomySortTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mem = Memory()
        self.trainer = Trainer(self.mem, self.profile, self.temp.name)
        for signature in self.profile["signatures"]:
            self.mem.put(signature["address"], bytes.fromhex(signature["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x202552C))
        self.mem.put(PARTY_COUNT, b"\1")
        self.mem.put(PARTY, sample().raw)
        self.layout = self.profile["economy"]
        for key, value in [
            ("coins", 735),
            ("beauty_points", 5),
            ("bracer_points", 220),
        ]:
            field = self.layout[key]
            self.mem.put(field["address"], value.to_bytes(field["size"], "little"))
        self.mem.put(0x202552C + 0x290, struct.pack("<IH", 9711707, 1234))

    def test_reads_expanded_coins_and_values_without_using_legacy_coins(self):
        snap = self.trainer.snapshot()
        self.assertEqual(
            (
                snap["money"],
                snap["coins"],
                snap["beauty_points"],
                snap["bracer_points"],
            ),
            (9711707, 735, 5, 220),
        )
        self.assertEqual(self.mem.writes, 0)

    def test_values_boundaries_write_restore_preserve_neighbours(self):
        for values in [(0, 0, 0, 0), (9999999, 999999999, 65535, 65535)]:
            snap = self.trainer.snapshot()
            before = dict(self.mem.data)
            patches = self.trainer.edit_values(snap, *values)
            changed_addresses = {a + i for a, b, c in patches for i in range(len(b))}
            result = self.trainer.commit(snap, patches, "values")
            after = self.trainer.snapshot()
            self.assertEqual(
                tuple(
                    after[k]
                    for k in ["money", "coins", "beauty_points", "bracer_points"]
                ),
                values,
            )
            self.assertTrue(
                all(
                    self.mem.data.get(a, 0) == b
                    for a, b in before.items()
                    if a not in changed_addresses
                )
            )
            self.trainer.restore(result["backup"])
            self.assertEqual(self.mem.data, before)

    def test_invalid_values_refused_before_writes(self):
        snap = self.trainer.snapshot()
        for index, value in [
            (0, 10000000),
            (1, 1000000000),
            (2, 65536),
            (3, -1),
            (2, True),
            (3, "bad"),
        ]:
            values = [
                snap[k] for k in ["money", "coins", "beauty_points", "bracer_points"]
            ]
            values[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                self.trainer.edit_values(snap, *values)
        self.assertEqual(self.mem.writes, 0)

    def test_stale_value_code_battle_and_money_key_block_all_writes(self):
        for address in [
            self.layout["coins"]["address"],
            self.layout["beauty_points"]["address"],
            self.layout["bracer_points"]["address"],
            self.layout["signatures"][0]["address"],
            0x2024588 + 0xF20,
            self.profile["battle_flag"]["address"],
            self.profile["trainer"]["pointer_address"],
        ]:
            with self.subTest(address=address):
                original = self.mem.read(address, 1)
                snap = self.trainer.snapshot()
                patches = self.trainer.edit_values(snap, 100, 736, 6, 221)
                self.mem.put(address, bytes([original[0] ^ 2]))
                with self.assertRaises((IOError, ValueError)):
                    self.trainer.commit(snap, patches, "stale")
                self.assertEqual(self.mem.writes, 0)
                self.mem.put(address, original)

    def test_nonzero_money_key_roundtrip_and_changed_key_refuses_restore(self):
        key = 0xAABBCCDD
        self.mem.put(0x2024588 + 0xF20, struct.pack("<I", key))
        self.mem.put(0x202552C + 0x290, struct.pack("<I", 500 ^ key))
        snap = self.trainer.snapshot()
        self.assertEqual(snap["money"], 500)
        result = self.trainer.commit(
            snap, self.trainer.edit_money(snap, 1000, 735), "money"
        )
        self.assertEqual(self.trainer.snapshot()["money"], 1000)
        self.mem.put(0x2024588 + 0xF20, struct.pack("<I", key + 1))
        writes = self.mem.writes
        with self.assertRaisesRegex(ValueError, "密钥"):
            self.trainer.restore(result["backup"])
        self.assertEqual(self.mem.writes, writes)
        self.mem.put(0x2024588 + 0xF20, struct.pack("<I", key))
        self.trainer.restore(result["backup"])
        self.assertEqual(self.trainer.snapshot()["money"], 500)

    def test_sort_all_pockets_stable_preserves_full_records_and_restores(self):
        for pocket in self.profile["pockets"]:
            with self.subTest(pocket=pocket["id"]):
                records = [
                    struct.pack("<HH", n, q)
                    for n, q in [(999, 0), (0, 7), (13, 4), (13, 0), (4, 164)]
                ]
                records += [b"\0" * 4] * (pocket["capacity"] - len(records))
                raw = b"".join(records)
                self.mem.put(pocket["address"], raw)
                snap = self.trainer.snapshot(pocket["id"])
                patches = self.trainer.sort_bag(snap)
                updated = patches[0][2]
                self.assertEqual(
                    Counter(records),
                    Counter(updated[i : i + 4] for i in range(0, len(updated), 4)),
                )
                self.assertEqual(
                    list(struct.iter_unpack("<HH", updated[:16])),
                    [(4, 164), (13, 4), (13, 0), (999, 0)],
                )
                self.assertEqual(updated[16:20], struct.pack("<HH", 0, 7))
                result = self.trainer.commit(snap, patches, "sort")
                sorted_snap = self.trainer.snapshot(pocket["id"])
                self.assertFalse(
                    self.trainer.commit(
                        sorted_snap, self.trainer.sort_bag(sorted_snap), "again"
                    )["changed"]
                )
                self.trainer.restore(result["backup"])
                self.assertEqual(self.mem.read(pocket["address"], len(raw)), raw)

    def test_sort_stale_tail_and_battle_refuse_before_any_writes(self):
        p = self.profile["pockets"][0]
        self.mem.put(p["address"], struct.pack("<HHHH", 15, 3, 13, 4))
        for address in [
            p["address"] + p["capacity"] * 4 - 1,
            self.profile["battle_flag"]["address"],
        ]:
            snap = self.trainer.snapshot()
            old = self.mem.read(address, 1)
            self.mem.put(address, bytes([old[0] ^ 2]))
            with self.assertRaises(IOError):
                self.trainer.commit(snap, self.trainer.sort_bag(snap), "stale sort")
            self.assertEqual(self.mem.writes, 0)
            self.mem.put(address, old)

    def test_large_sort_requires_new_bridge_before_backup(self):
        p = self.profile["pockets"][0]
        self.mem.put(p["address"], struct.pack("<HHHH", 15, 3, 13, 4))
        self.mem.capabilities = {"BATCH", "ROMCRC", "CRCBATCH"}
        snap = self.trainer.snapshot()
        with self.assertRaisesRegex(ValueError, "重新加载"):
            self.trainer.commit(snap, self.trainer.sort_bag(snap), "old bridge")
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])
        self.assertEqual(self.mem.writes, 0)
