import json
import unittest
from pathlib import Path
from move_sources import describe_move_sources


class MoveSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))

    def test_evolution_level_and_unverified_are_distinguished(self):
        rows = describe_move_sources(160, 48, [757, 242, 8, 700], self.profile)
        self.assertEqual(
            [r["status"] for r in rows],
            ["evolution", "level", "other-unverified", "level"],
        )
        self.assertIn("未核对", rows[2]["text"])

    def test_above_level_is_not_declared_illegal(self):
        partial = {**self.profile, "tm_compatibility": {}}
        rows = describe_move_sources(458, 44, [423], partial)
        self.assertEqual(rows[0]["status"], "higher-level")
        self.assertEqual(rows[0]["levels"], [49])
        self.assertIn("其他途径未核对", rows[0]["text"])

    def test_machine_can_explain_move_above_level_threshold(self):
        rows = describe_move_sources(458, 44, [423], self.profile)
        self.assertEqual(rows[0]["status"], "machine")
        self.assertEqual(rows[0]["levels"], [49])
        self.assertTrue(rows[0]["machines"])

    def test_user_reported_hms_match_exact_rom_item_mapping(self):
        self.assertEqual(self.profile["items"]["341"]["move"], 57)
        self.assertEqual(self.profile["items"]["342"]["move"], 70)
        self.assertEqual(self.profile["items"]["341"]["tm_index"], 122)
        self.assertEqual(
            len([v for v in self.profile["items"].values() if "tm_index" in v]), 128
        )

    def test_empty_slots_and_unknown_species(self):
        self.assertEqual(describe_move_sources(160, 50, [0] * 4, self.profile), [])
        self.assertEqual(
            describe_move_sources(65535, 50, [33], self.profile)[0]["status"],
            "unverified",
        )

    def test_profile_has_complete_index_for_supported_species(self):
        self.assertEqual(
            set(self.profile["species"]), set(self.profile["level_up_learnsets"])
        )
        for entries in self.profile["level_up_learnsets"].values():
            self.assertTrue(
                all(0 <= level <= 100 and 0 < move <= 1023 for move, level in entries)
            )


if __name__ == "__main__":
    unittest.main()
