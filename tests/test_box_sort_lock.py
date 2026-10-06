import json
import struct
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from box_data import BoxPokemon
from box_preferences import BoxPreferences
from rom_versions import load_profile
from tests.test_box import packed_box
from tests.test_core import Memory
from tests.test_rom_versions import install_profile
from trainer_core import PARTY_COUNT, SAVE_POINTER, Trainer


def record(species, identity):
    raw = bytearray(packed_box())
    struct.pack_into("<H", raw, 28, species)
    raw[18] = identity % 256  # Unknown byte must follow the individual.
    struct.pack_into("<I", raw, 0, identity)
    raw[39:44] = (757).to_bytes(5, "little")
    return bytes(raw)


class BoxSortLockTests(unittest.TestCase):
    def setup_release(self, folder, crc="4755f497"):
        profile = load_profile(crc=crc)
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\0")
        return (
            memory,
            Trainer(memory, profile, folder),
            profile["storage"]["box_addresses"],
        )

    def test_full_750_global_stable_sort_and_whole_restore_both_versions(self):
        for crc in ("b4af11c8", "4755f497"):
            with self.subTest(crc=crc), tempfile.TemporaryDirectory() as folder:
                memory, trainer, addresses = self.setup_release(folder, crc)
                originals = []
                for box, address in enumerate(addresses):
                    raw = b"".join(
                        record(160 if (box * 30 + slot) % 3 else 1, box * 30 + slot)
                        for slot in range(30)
                    )
                    originals.append(raw)
                    memory.put(address, raw)
                prepared = trainer.prepare_box_sort()
                self.assertEqual(prepared["count"], 750)
                expected = sorted(
                    [raw[i : i + 58] for raw in originals for i in range(0, 1740, 58)],
                    key=lambda raw: BoxPokemon(raw).species,
                )
                result = trainer.commit_box_sort(prepared)
                actual = [
                    memory.read(a + slot * 58, 58)
                    for a in addresses
                    for slot in range(30)
                ]
                self.assertEqual(actual, expected)
                self.assertEqual(
                    Counter(actual),
                    Counter(
                        raw[i : i + 58] for raw in originals for i in range(0, 1740, 58)
                    ),
                )
                self.assertFalse(
                    trainer.commit_box_sort(trainer.prepare_box_sort())["changed"]
                )
                trainer.restore(result["backup"])
                self.assertEqual([memory.read(a, 1740) for a in addresses], originals)

    def test_locked_box_stays_intact_global_packs_across_unlocked_boxes(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            untouched = record(160, 1) * 30
            memory.put(addresses[0], untouched)
            memory.put(addresses[24], record(160, 2) + record(1, 3) + record(1, 4))
            trainer.locked_boxes = {0}
            result = trainer.commit_box_sort(trainer.prepare_box_sort())
            self.assertEqual(memory.read(addresses[0], 1740), untouched)
            self.assertEqual(
                memory.read(addresses[1], 174),
                record(1, 3) + record(1, 4) + record(160, 2),
            )
            self.assertEqual(memory.read(addresses[24], 1740), b"\0" * 1740)
            trainer.restore(result["backup"])
            self.assertEqual(memory.read(addresses[0], 1740), untouched)

    def test_unknown_residue_species_and_all_locked_refuse_before_write(self):
        for raw in (b"\xa5" + b"\0" * 57, record(65535, 1)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as folder:
                memory, trainer, addresses = self.setup_release(folder)
                memory.put(addresses[0], raw)
                with self.assertRaises(ValueError):
                    trainer.prepare_box_sort()
                self.assertEqual(memory.writes, 0)
        with tempfile.TemporaryDirectory() as folder:
            _, trainer, _ = self.setup_release(folder)
            trainer.locked_boxes = set(range(25))
            with self.assertRaisesRegex(ValueError, "全部盒子"):
                trainer.prepare_box_sort()

    def test_stale_changed_or_unchanged_box_blocks_entire_sort(self):
        for box in (0, 24):
            with self.subTest(box=box), tempfile.TemporaryDirectory() as folder:
                memory, trainer, addresses = self.setup_release(folder)
                memory.put(addresses[0], record(160, 1) + record(1, 2))
                prepared = trainer.prepare_box_sort()
                memory.put(addresses[box] + 18, b"\xa5")
                before = dict(memory.data)
                with self.assertRaises(OSError):
                    trainer.commit_box_sort(prepared)
                self.assertEqual(memory.data, before)
                self.assertEqual(memory.writes, 0)

    def test_new_locks_block_prepared_edit_move_sort_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            memory.put(addresses[0], record(160, 1) + record(1, 2))
            snap = trainer.snapshot_box(0)
            patches, _ = trainer.edit_box(snap, 0, friendship=99)
            sort = trainer.prepare_box_sort()
            trainer.locked_boxes = {0}
            for callback in (
                lambda: trainer.edit_box(snap, 0, friendship=99),
                lambda: trainer.commit_box(patches, "bypass"),
                lambda: trainer.prepare_box_move(snap, 0, trainer.snapshot_box(1)),
                lambda: trainer.prepare_box_move(trainer.snapshot_box(1), 0, snap),
                lambda: trainer.commit_box_sort(sort),
            ):
                with self.assertRaisesRegex(ValueError, "锁"):
                    callback()
            self.assertEqual(memory.writes, 0)
            trainer.locked_boxes.clear()
            result = trainer.commit_box_sort(trainer.prepare_box_sort())
            trainer.locked_boxes = {0}
            before = dict(memory.data)
            with self.assertRaisesRegex(ValueError, "锁"):
                trainer.restore(result["backup"])
            self.assertEqual(memory.data, before)

    def test_large_sort_requires_bridge_capability_before_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            memory.put(addresses[0], record(160, 1) + record(1, 2))
            memory.capabilities = Memory.capabilities - {"BOXBATCH"}
            with self.assertRaisesRegex(ValueError, "BOXBATCH"):
                trainer.commit_box_sort(trainer.prepare_box_sort())
            self.assertEqual(memory.writes, 0)
            self.assertFalse(list(Path(folder).glob("*.json")))

    def test_interrupted_sort_preserves_whole_originals_and_refuses_blind_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            original = record(160, 1) + record(1, 2) + b"\0" * 58 * 28
            memory.put(addresses[0], original)

            def interrupted(patches, **kwargs):
                changed = next(p for p in patches if p[1] != p[2])
                memory.put(changed[0], changed[2][:58])
                raise OSError("interrupted box sort")

            memory.batch = interrupted
            with self.assertRaisesRegex(OSError, "interrupted"):
                trainer.commit_box_sort(trainer.prepare_box_sort())
            path = next(Path(folder).glob("*.json"))
            backup = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(backup["status"], "failed-or-unconfirmed")
            self.assertEqual(bytes.fromhex(backup["patches"][0]["before"]), original)
            with self.assertRaisesRegex(ValueError, "未被确认"):
                trainer.restore(path)

    def test_egg_ready_requires_egg_preserves_raw_and_restores(self):
        with tempfile.TemporaryDirectory() as folder:
            memory, trainer, addresses = self.setup_release(folder)
            normal = record(160, 1)
            egg, _ = BoxPokemon(normal).edit(trainer.profile, egg=True)
            memory.put(addresses[0], normal + egg.raw)
            snap = trainer.snapshot_box(0)
            with self.assertRaisesRegex(ValueError, "仅适用于"):
                trainer.prepare_box_egg_ready(snap, 0)
            patches, _ = trainer.prepare_box_egg_ready(snap, 1)
            self.assertEqual(
                [i for i in range(58) if patches[0][1][i] != patches[0][2][i]], [37]
            )
            result = trainer.commit_box(patches, "egg ready")
            actual = BoxPokemon(memory.read(addresses[0] + 58, 58))
            self.assertTrue(actual.egg)
            self.assertEqual(actual.friendship, 0)
            self.assertEqual(memory.read(addresses[0], 58), normal)
            trainer.restore(result["backup"])
            self.assertEqual(memory.read(addresses[0] + 58, 58), egg.raw)


class LocalBoxPreferencesTests(unittest.TestCase):
    def test_local_locks_reload_and_are_separate_by_save_and_rom(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "box-locks.json"
            save = Path(folder) / "game.sav"
            save.write_bytes(b"private save bytes")
            store = BoxPreferences(path)
            store.save("rom1", save, {0, 24})
            self.assertEqual(BoxPreferences(path).load("rom1", save), {0, 24})
            self.assertEqual(store.load("rom2", save), set())
            self.assertEqual(store.load("rom1", Path(folder) / "other.sav"), set())
            self.assertEqual(save.read_bytes(), b"private save bytes")
            self.assertNotIn(str(save), path.read_text())
            store.save("rom1", save, set())
            self.assertEqual(store.load("rom1", save), set())

    def test_invalid_local_preferences_fail_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "box-locks.json"
            store = BoxPreferences(path)
            path.write_text(json.dumps({"schema": 9, "saves": {}}))
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                store.save("rom", "game.sav", {0})
            self.assertEqual(path.read_bytes(), before)
