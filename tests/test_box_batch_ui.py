"""Hidden-Tk checks for PC reference staging and one batch transaction."""

import struct
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from box_data import BoxPokemon
from trainer_app import App
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer
from tests.test_box import packed_box
from tests.test_core import Memory
from tests.test_pokemon_data import sample


class BoxBatchUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.addCleanup(self.app.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mem = Memory()
        for signature in self.app.profile["signatures"] + self.app.profile["storage"]["signatures"]:
            self.mem.put(signature["address"], bytes.fromhex(signature["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.mem.put(PARTY_COUNT, b"\1")
        self.mem.put(PARTY, sample().raw)
        self.app.mem = self.mem
        self.app.trainer = Trainer(self.mem, self.app.profile, self.temp.name)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.run = lambda label, job, done: done(job())
        self.mem.close = lambda: None
        self.addresses = self.app.profile["storage"]["box_addresses"]

    def put_box(self, index, records):
        self.mem.put(self.addresses[index], b"".join(records) + b"\0" * 58 * (30 - len(records)))

    def read_box(self, index):
        self.app.apply_box_snapshot(self.app.trainer.snapshot_box(index))

    def test_shift_toggles_individual_slots_and_staging_keeps_originals(self):
        self.put_box(0, [packed_box()] * 3)
        self.put_box(1, [packed_box()])
        self.read_box(0)
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()
        self.app.box_tree.toggle("1")
        self.app.select_box_mon()
        self.app.box_tree.toggle("0")
        self.app.select_box_mon()
        self.assertEqual(self.app.box_tree.selection(), ("1",))
        self.assertEqual(set(self.app.box_selected_refs), {(0, 1)})
        self.app.stage_box_selection()
        self.assertEqual(set(self.app.box_staged_refs), {(0, 1)})
        self.assertEqual(self.mem.read(self.addresses[0] + 58, 58), packed_box())
        self.read_box(1)
        self.app.box_tree.toggle("0")
        self.app.select_box_mon()
        self.assertEqual(set(self.app.box_operation_references()[i]["box"] for i in range(2)), {0, 1})
        self.assertEqual(self.app.box_stage_tree.get_children(), ("0:1",))
        self.assertEqual(self.mem.writes, 0)

    def test_hidden_tk_shift_click_and_stage_keyboard_selection(self):
        self.put_box(0, [packed_box(), packed_box()])
        self.read_box(0)
        self.app.box_tree.cards[0].event_generate("<Shift-Button-1>")
        self.root.update()
        self.assertEqual(self.app.box_tree.selection(), ("0",))
        self.app.box_tree.cards[1].event_generate("<Shift-Button-1>")
        self.root.update()
        self.assertEqual(set(self.app.box_tree.selection()), {"0", "1"})
        self.app.stage_box_selection()
        self.app.select_box_stage("0:0")
        self.app.box_stage_tree.focus("0:1")
        self.app.box_stage_key(type("Event", (), {"state": 0x0001})())
        self.assertEqual(set(self.app.box_stage_selected), {(0, 0), (0, 1)})

    def test_preview_confirmation_precedes_commit_and_cancel_does_not_write(self):
        self.put_box(0, [packed_box()])
        self.read_box(0)
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()
        self.app.box_move_target.set("2")
        with patch("trainer_app.messagebox.askokcancel", return_value=False) as confirm:
            self.app.move_box_mon()
        self.assertIn("第2盒", confirm.call_args.args[1])
        self.assertEqual(self.mem.writes, 0)
        with patch("trainer_app.messagebox.askokcancel", return_value=True):
            self.app.move_box_mon()
        self.assertEqual(self.mem.read(self.addresses[0], 58), b"\0" * 58)
        self.assertEqual(self.mem.read(self.addresses[1], 58), packed_box())

    def test_changed_source_remains_stale_after_refresh_and_shift(self):
        self.put_box(0, [packed_box(), packed_box()])
        self.read_box(0)
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()
        original = self.app.box_selected_refs[(0, 0)]["raw"]
        changed = bytearray(original)
        changed[18] = 1
        self.mem.put(self.addresses[0], bytes(changed))
        self.read_box(0)
        self.assertIn("原槽已变化", self.app.status.get())
        self.app.box_tree.toggle("1")
        self.app.select_box_mon()
        self.assertEqual(self.app.box_selected_refs[(0, 0)]["raw"], original)
        self.app.box_move_target.set("2")
        with patch("trainer_app.messagebox.showerror") as error:
            # Synchronous test runner lets the UI display the backend rejection.
            try:
                self.app.move_box_mon()
            except ValueError as exc:
                self.assertIn("原槽已变化", str(exc))
            else:
                error.assert_called_once()
        self.assertEqual(self.mem.writes, 0)

    def test_mixed_egg_menu_disabled_and_reconnect_clears_staging(self):
        egg = bytearray(packed_box())
        egg[19] |= 4
        egg[57] |= 64
        self.put_box(0, [bytes(egg), packed_box()])
        self.read_box(0)
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()
        self.app.box_tree.toggle("1")
        self.app.select_box_mon()
        menu = self.app.make_box_batch_menu(self.app.box_operation_references())
        self.assertEqual(menu.entrycget(2, "state"), "disabled")
        self.app.stage_box_selection()
        self.assertEqual(len(self.app.box_staged_refs), 2)
        with patch.object(self.app, "run", return_value=None):
            self.app.connect()
        self.assertFalse(self.app.box_operation_references())
        self.assertFalse(self.app.box_staged_refs)
        self.assertEqual(self.mem.writes, 0)

    def test_empty_and_locked_sources_cannot_be_staged_or_moved(self):
        self.put_box(0, [packed_box()])
        self.read_box(0)
        self.app.box_tree.selection_set("1")
        self.app.select_box_mon()
        with patch("trainer_app.messagebox.showinfo") as info:
            self.app.stage_box_selection()
            info.assert_called_once()
        self.assertFalse(self.app.box_staged_refs)
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()
        self.app.trainer.locked_boxes = {0}
        menu = self.app.make_box_batch_menu(self.app.box_operation_references())
        self.assertEqual(menu.entrycget(0, "state"), "disabled")
        self.assertEqual(menu.entrycget(1, "state"), "disabled")
        with patch("trainer_app.messagebox.showerror") as error:
            self.app.stage_box_selection()
            error.assert_called_once()
        self.assertEqual(self.mem.writes, 0)


if __name__ == "__main__":
    unittest.main()
