import unittest
from unittest.mock import patch

from box_data import BoxPokemon
from tests.test_box import packed_box


class EmptySlotUiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_app import AppTests
        AppTests.setUp(self)

    def test_only_next_party_slot_opens_creation(self):
        self.assertEqual(len(self.app.snapshot["party"]), 1)
        self.assertNotIn("disabled", self.app.party_tree.cards[1].state())
        self.assertIn("disabled", self.app.party_tree.cards[2].state())
        self.assertIn("先填前一位", self.app.party_tree.cards[2].cget("text"))
        with patch.object(self.app.creation_page, "open_for_target") as opened:
            self.app.party_tree.choose(2)
            opened.assert_not_called()
            self.app.party_tree.choose(1)
            opened.assert_called_once_with("party_empty", slot=1)
        self.assertEqual(self.mem.writes, 0)

    def test_pc_empty_slot_uses_exact_box_and_slot_and_lock_blocks(self):
        raw = packed_box() + bytes(58 * 29)
        self.app.apply_box_snapshot({
            "index": 24,
            "address": self.app.profile["storage"]["box_addresses"][24],
            "raw": raw,
            "pokemon": tuple(BoxPokemon(raw[i:i + 58])
                             for i in range(0, len(raw), 58)),
        })
        self.app.box_tree.selection_set("29")
        self.root.update()
        self.assertNotIn("disabled", self.app.box_create_button.state())
        with patch.object(self.app.creation_page, "open_for_target") as opened:
            self.app.create_selected_box_slot()
            opened.assert_called_once_with("pc_empty", box=24, slot=29)
        self.app.trainer.locked_boxes = {24}
        self.app.update_box_lock()
        self.assertIn("disabled", self.app.box_create_button.state())
        with patch.object(self.app.creation_page, "open_for_target") as opened:
            self.app.open_box_create_slot(29)
            opened.assert_not_called()
        self.assertEqual(self.mem.writes, 0)


if __name__ == "__main__":
    unittest.main()
