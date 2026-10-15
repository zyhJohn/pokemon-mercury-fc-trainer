import hashlib
import unittest

from unown_records import (CAPTURE_ADDRESS, CAPTURE_BYTES, FINAL_ADDRESS, LAYOUT,
                           RESEARCH_ADDRESS, completion_changes, flag_address, prepare_unown_records_patches,
                           read_unown_records, unown_rom_features)


SHA = next(iter(LAYOUT["releases"]))


class Memory:
    def __init__(self):
        self.data = bytearray(0x40000)
        self.pointers = bytes.fromhex("0050000200600002")

    def read(self, address, size):
        if address == 0x03005008:
            return self.pointers[:size]
        start = address - 0x02000000
        return bytes(self.data[start:start + size])

    def put(self, address, raw):
        start = address - 0x02000000
        self.data[start:start + len(raw)] = raw


class UnownRecordsTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory()

    def test_layout_and_fields(self):
        snapshot = read_unown_records(self.memory, SHA)
        self.assertEqual(CAPTURE_BYTES, 4)
        self.assertEqual(len(snapshot["fields"]), 35)
        self.assertEqual(sum(field["editable"] for field in snapshot["fields"]), 34)
        self.assertEqual(snapshot["captured_count"], 0)
        self.assertEqual(flag_address(0x16EA), CAPTURE_ADDRESS)
        self.assertEqual(flag_address(0x1705), CAPTURE_ADDRESS + 3)
        self.assertFalse(snapshot["fields"][-1]["editable"])

    def test_every_release_guards_complete_audited_reward_script(self):
        script_start, script_end = 0x08EC83C6, 0x08EC8642
        audited_hash = "435d12dc5a864f7a2387e9086c43a90f6a99ad5ed733008b625320761ab1cad4"
        for sha, release in LAYOUT["releases"].items():
            with self.subTest(version=release["version"]):
                features = unown_rom_features(sha)
                self.assertEqual(len(features), 8)
                self.assertEqual(sum(len(raw) for _, raw in features), 866)
                matching = [(address, raw) for address, raw in features
                            if address <= script_start and address + len(raw) >= script_end]
                self.assertEqual(len(matching), 1)
                address, raw = matching[0]
                script = raw[script_start - address:script_end - address]
                self.assertEqual(len(script), 636)
                self.assertEqual(hashlib.sha256(script).hexdigest(), audited_hash)
                self.assertEqual(release["npc_script_sha256"], audited_hash)
                # Completion flag and each fossil reward remain inside the
                # compared bytes, including the final script byte before end.
                self.assertEqual(script[0x08EC861E - script_start:0x08EC8621 - script_start],
                                 bytes.fromhex("295217"))
                for changed_at in (script_start, 0x08EC85B5, 0x08EC861F, script_end - 1):
                    changed = bytearray(script)
                    changed[changed_at - script_start] ^= 1
                    self.assertNotEqual(hashlib.sha256(changed).hexdigest(), audited_hash)

    def test_every_form_is_independent_and_unknown_bits_survive(self):
        self.memory.put(CAPTURE_ADDRESS, bytes.fromhex("030000c0"))
        for letter in LAYOUT["letters"]:
            snapshot = read_unown_records(self.memory, SHA)
            before = bytes(self.memory.data)
            patches = prepare_unown_records_patches(snapshot, {"captured_" + letter: True})
            self.assertEqual(len(patches), 1)
            patch = patches[0]
            self.assertEqual(patch.before, bytes.fromhex("030000c0"))
            self.assertEqual(bytes(self.memory.data), before)
            self.memory.put(patch.address, patch.after)
            changed = read_unown_records(self.memory, SHA)
            self.assertEqual(changed["captured_count"], 1)
            self.assertEqual(sum(field["value"] for field in changed["fields"][:28]), 1)
            self.assertEqual(patch.after[0] & 3, 3)
            self.assertEqual(patch.after[-1] & 0xC0, 0xC0)
            self.memory.put(CAPTURE_ADDRESS, patch.before)

    def test_all_captured_does_not_change_research_final_or_neighbours(self):
        self.memory.put(CAPTURE_ADDRESS, bytes.fromhex("030000c0"))
        self.memory.put(RESEARCH_ADDRESS, b"\xd2")
        self.memory.put(FINAL_ADDRESS, b"\xa5")
        snapshot = read_unown_records(self.memory, SHA)
        patches = prepare_unown_records_patches(snapshot, {"captured_" + letter: True for letter in LAYOUT["letters"]})
        self.assertEqual([(p.address, p.after) for p in patches], [(CAPTURE_ADDRESS, b"\xff" * 4)])
        self.memory.put(CAPTURE_ADDRESS, patches[0].after)
        clear = prepare_unown_records_patches(read_unown_records(self.memory, SHA),
                                               {"captured_" + letter: False for letter in LAYOUT["letters"]})
        self.assertEqual(clear[0].after, bytes.fromhex("030000c0"))

    def test_explicit_research_changes_keep_other_flags(self):
        self.memory.put(RESEARCH_ADDRESS, b"\xd2")
        snapshot = read_unown_records(self.memory, SHA)
        changes = {note["key"]: True for note in LAYOUT["notes"] if note["editable"]}
        patches = prepare_unown_records_patches(snapshot, changes)
        self.assertEqual([(p.address, p.before, p.after) for p in patches], [(RESEARCH_ADDRESS, b"\xd2", b"\xff")])

    def test_reject_final_unknown_non_bool_and_unknown_rom(self):
        snapshot = read_unown_records(self.memory, SHA)
        for changes in ({"note_final": True}, {"puzzle": True}, {"captured_A": 1}, {"captured_A": "True"}):
            with self.assertRaises(ValueError):
                prepare_unown_records_patches(snapshot, changes)
        with self.assertRaises(ValueError):
            read_unown_records(self.memory, "0" * 64)

    def test_noop(self):
        snapshot = read_unown_records(self.memory, SHA)
        self.assertEqual(prepare_unown_records_patches(snapshot, {}), ())
        self.assertEqual(prepare_unown_records_patches(snapshot, {"captured_A": False}), ())

    def test_reject_bad_pointers_or_short_records(self):
        for pointers in (bytes(8), bytes.fromhex("0150000200600002")):
            self.memory.pointers = pointers
            with self.assertRaises(ValueError):
                read_unown_records(self.memory, SHA)
        class Short(Memory):
            def read(self, address, size):
                return super().read(address, size)[:-1]
        with self.assertRaises(ValueError):
            read_unown_records(Short(), SHA)

    def test_reject_changing_snapshot(self):
        class Moving(Memory):
            count = 0
            def read(self, address, size):
                if address == CAPTURE_ADDRESS:
                    self.count += 1
                    return bytes([self.count]) * size
                return super().read(address, size)
        with self.assertRaises(ValueError):
            read_unown_records(Moving(), SHA)

    def test_reject_tampered_snapshot_ranges(self):
        snapshot = read_unown_records(self.memory, SHA)
        snapshot["records"][0x02000000] = b"\x00"
        with self.assertRaises(ValueError):
            prepare_unown_records_patches(snapshot, {"captured_A": True})

    def test_completion_draft_requires_accepted_unfinished_quest(self):
        for status in (0, 4, 6):
            self.memory.put(FINAL_ADDRESS, bytes((status,)))
            with self.assertRaises(ValueError):
                completion_changes(read_unown_records(self.memory, SHA))
        self.memory.put(FINAL_ADDRESS, b"\xA2")
        snapshot = read_unown_records(self.memory, SHA)
        self.assertTrue(snapshot["task_accepted"])
        self.assertFalse(snapshot["task_complete"])
        changes = completion_changes(snapshot)
        self.assertEqual(len(changes), 34)
        self.assertTrue(all(value is True for value in changes.values()))
        patches = prepare_unown_records_patches(snapshot, changes)
        self.assertEqual(len(patches), 2)
        self.assertNotIn(FINAL_ADDRESS, [patch.address for patch in patches])


if __name__ == "__main__":
    unittest.main()
