import unittest
from pokemon_data import change_nature_pid, unown_form, calculate_stats
from tests.test_pokemon_data import sample


class NatureTests(unittest.TestCase):
    def test_all_natures_preserve_gender_parity_shiny_and_flags(self):
        for shiny in (False, True):
            original, _ = sample().edit(shiny=shiny)
            for nature in range(25):
                edited, report = original.edit(
                    nature=nature, base=[85, 105, 100, 78, 79, 83]
                )
                self.assertEqual(edited.pid % 25, nature)
                self.assertEqual(edited.pid & 255, original.pid & 255)
                self.assertEqual(edited.shiny, shiny)
                self.assertEqual(edited.raw[72:76], original.raw[72:76])
                self.assertEqual(edited.raw[28:32], original.raw[28:32])
                self.assertTrue(report["stats_match"])

    def test_combined_nature_shiny_and_ability_preserves_requested_results(self):
        mon = sample()
        for nature in range(25):
            edited, _ = mon.edit(
                nature=nature,
                shiny=True,
                ability_slot=0,
                abilities=[67, 89, 125],
                gender_ratio=31,
                base=[85, 105, 100, 78, 79, 83],
            )
            self.assertEqual(edited.pid % 25, nature)
            self.assertTrue(edited.shiny)
            self.assertEqual(edited.pid & 1, 0)
            self.assertEqual(edited.ability_flag, 0)
            self.assertEqual(
                edited.stats,
                calculate_stats(
                    [85, 105, 100, 78, 79, 83], mon.ivs, mon.evs, mon.level, nature
                ),
            )

    def test_unown_form_and_spinda_guard(self):
        pid = 0x12345678
        otid = 0xABCDEF01
        for nature in range(25):
            result = change_nature_pid(pid, otid, nature, 201)
            self.assertEqual(unown_form(result), unown_form(pid))
        with self.assertRaises(ValueError):
            change_nature_pid(pid, otid, (pid % 25 + 1) % 25, 308)
        self.assertEqual(change_nature_pid(pid, otid, pid % 25, 308), pid)


if __name__ == "__main__":
    unittest.main()
