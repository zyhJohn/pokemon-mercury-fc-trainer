"""UI checks for creation preview and transaction boundaries."""

import json
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import Mock, patch

from creation_page import CreationPage
from pokemon_creation import create_box_pokemon


class CreationPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.profile = json.loads(Path("rom_profile_v12.json").read_text("utf-8"))
        self.names = json.loads(Path("names.json").read_text("utf-8"))
        self.mon = create_box_pokemon(
            self.profile, species=1, level=5, pid=0, otid=0, nickname="MON",
            ot_name="OT", moves=(1, 0, 0, 0), ball=4, met_location=0)
        self.prepared = {
            "pokemon": self.mon, "destination": (0, 0),
            "connection_generation": 1, "replaced": None,
        }
        self.trainer = SimpleNamespace(
            profile=self.profile, connection_generation=1,
            prepare_create_box=Mock(return_value=self.prepared),
            prepare_create_party=Mock(return_value={**self.prepared, "destination": ("party", 0)}),
            commit_create_box=Mock(return_value={"changed": True}),
            commit_create_party=Mock(return_value={"changed": True}),
            snapshot_box=Mock(return_value={"index": 0}),
            snapshot=Mock(return_value={"party": []}),
            read_player_ot=Mock(return_value=({"ot_tid": 42, "ot_sid": 7,
                                             "ot_gender": 1, "ot_name": "PLAYER"}, "已填入")),
        )
        self.app = SimpleNamespace(
            root=self.root, nb=ttk.Notebook(self.root), trainer=self.trainer,
            busy=False, names=self.names, status=tk.StringVar(),
            discard_box_changes=Mock(return_value=True),
            discard_party_changes=Mock(return_value=True),
            apply_box_snapshot=Mock(), apply_snapshot=Mock(),
        )
        self.app.run = lambda label, job, done: done(job())
        self.page = CreationPage(self.app, self.app.nb)

    def test_named_choices_and_draft_reach_backend(self):
        self.assertIn("1 - ", self.page.species_widget["values"][0])
        self.assertTrue(any(value.startswith("4 - ") for value in self.page.ball_widget["values"]))
        self.page.species.set(next(v for v in self.page.species_widget["values"] if v.startswith("1 - ")))
        self.page.moves[0].set(next(v for v in self.page.move_widgets[0]["values"] if v.startswith("1 - ")))
        self.page.preview()
        args = self.trainer.prepare_create_box.call_args.args
        self.assertEqual(args[0], 0)
        self.assertEqual(args[1]["species"], 1)
        self.assertEqual(args[1]["moves"], [1, 0, 0, 0])
        self.assertEqual(args[1]["ball"], 4)
        self.assertIn("特性", self.page.detail.get())

    def test_exact_species_and_move_names_resolve_to_rom_ids(self):
        self.page.species.set(self.names["breeds"]["1"])
        self.page.moves[0].set(self.names["skills"]["1"])
        draft = self.page.draft()
        self.assertEqual((draft["species"], draft["moves"][0]), (1, 1))

    def test_edit_or_target_change_invalidates_and_cannot_commit(self):
        self.page.preview()
        self.assertIsNotNone(self.page.prepared)
        self.page.level.set("6")
        self.assertIsNone(self.page.prepared)
        self.page.commit()
        self.trainer.commit_create_box.assert_not_called()
        self.page.preview()
        self.page.box.set("2")
        self.assertIsNone(self.page.prepared)

    def test_prepare_result_after_change_or_reconnect_is_discarded(self):
        pending = []
        self.app.run = lambda label, job, done: pending.append((job, done))
        self.page.preview()
        job, done = pending.pop()
        result = job()
        self.page.nickname.set("OTHER")
        done(result)
        self.assertIsNone(self.page.prepared)
        self.page.preview()
        job, done = pending.pop()
        result = job()
        self.trainer.connection_generation += 1
        done(result)
        self.assertIsNone(self.page.prepared)
        # A stale prepared object is rejected even if a caller retained it.
        self.page.prepared = result
        self.page.preview_trainer = self.trainer
        self.page.preview_key = self.page._key()
        with patch("creation_page.messagebox.askokcancel") as confirm:
            self.page.commit()
        confirm.assert_not_called()
        self.trainer.commit_create_box.assert_not_called()

    def test_busy_and_disconnect_disable_actions(self):
        self.page.preview()
        self.app.busy = True
        self.page.freeze()
        self.assertIn("disabled", self.page.species_widget.state())
        self.assertIn("disabled", self.page.commit_button.state())
        self.app.busy = False
        self.page.thaw()
        self.assertNotIn("disabled", self.page.preview_button.state())
        self.app.trainer = None
        self.page.invalidate()
        self.page.thaw()
        self.assertIn("disabled", self.page.preview_button.state())

    def test_party_replacement_requires_slot_and_shows_old_member(self):
        self.page.mode.set("顶替队伍成员")
        self.page.preview()
        self.trainer.prepare_create_party.assert_not_called()
        self.assertIn("明确选择", self.app.status.get())
        self.page.replacement.set("2")
        from tests.test_pokemon_data import sample
        replacement = {**self.prepared, "destination": ("party", 1), "replaced": sample()}
        self.trainer.prepare_create_party.return_value = replacement
        self.page.preview()
        self.trainer.prepare_create_party.assert_called_once()
        self.assertEqual(self.trainer.prepare_create_party.call_args.kwargs,
                         {"replacement_slot": 1})
        self.assertIn("将顶替", self.page.detail.get())
        with patch("creation_page.messagebox.askokcancel", return_value=True):
            self.page.commit()
        self.trainer.commit_create_party.assert_called_once_with(replacement)
        self.app.apply_snapshot.assert_called_once_with({"party": []})

    def test_pc_empty_entry_pins_exact_slot_then_manual_target_change_unpins(self):
        pinned = {**self.prepared, "destination": (2, 7)}
        self.trainer.prepare_create_box.return_value = pinned
        self.page.open_for_target("pc_empty", box=2, slot=7)
        self.assertEqual(self.app.nb.select(), str(self.page.tab))
        self.assertEqual(self.page.box.get(), "3")
        self.page.preview()
        self.trainer.prepare_create_box.assert_called_once()
        self.assertEqual(self.trainer.prepare_create_box.call_args.args[0], 2)
        self.assertEqual(self.trainer.prepare_create_box.call_args.kwargs["slot"], 7)
        self.assertIn("位置 8", self.page.detail.get())
        self.page.box.set("4")
        self.assertIsNone(self.page.prepared)
        self.trainer.prepare_create_box.reset_mock()
        self.page.preview()
        self.assertNotIn("slot", self.trainer.prepare_create_box.call_args.kwargs)

    def test_exact_pc_slot_mismatch_refuses_preview(self):
        self.page.select_target("pc_empty", box=2, slot=7)
        self.trainer.prepare_create_box.return_value = {
            **self.prepared, "destination": (2, 8)}
        with self.assertRaisesRegex(ValueError, "所选空槽已变化"):
            self.page.preview()
        self.assertIsNone(self.page.prepared)

    def test_switching_pinned_slots_in_same_box_discards_old_preview(self):
        self.page.open_for_target("pc_empty", box=2, slot=0)
        self.trainer.prepare_create_box.return_value = {
            **self.prepared, "destination": (2, 0)}
        self.page.preview()
        self.assertIsNotNone(self.page.prepared)
        self.page.open_for_target("pc_empty", box=2, slot=1)
        self.assertIsNone(self.page.prepared)
        self.assertIn("disabled", self.page.commit_button.state())
        with patch("creation_page.messagebox.askokcancel") as confirm:
            self.page.commit()
        confirm.assert_not_called()
        self.trainer.commit_create_box.assert_not_called()

    def test_party_empty_entry_only_accepts_selected_append_slot(self):
        self.page.open_for_target("party_empty", slot=2)
        self.assertEqual(self.app.nb.select(), str(self.page.tab))
        self.trainer.prepare_create_party.return_value = {
            **self.prepared, "destination": ("party", 2)}
        self.page.preview()
        self.assertEqual(self.trainer.prepare_create_party.call_args.kwargs,
                         {"replacement_slot": None})
        self.assertIn("队伍第 3 位", self.page.detail.get())
        self.page.invalidate()
        self.trainer.prepare_create_party.return_value = {
            **self.prepared, "destination": ("party", 3)}
        with self.assertRaisesRegex(ValueError, "所选空槽已变化"):
            self.page.preview()
        self.assertIsNone(self.page.prepared)

    def test_player_ot_only_fills_draft(self):
        self.page.fill_player_ot()
        self.assertEqual(self.page.tid.get(), "42")
        self.assertEqual(self.page.sid.get(), "7")
        self.assertEqual(self.page.ot_name.get(), "PLAYER")
        self.trainer.commit_create_box.assert_not_called()

    def test_trainer_ids_are_rejected_before_combining(self):
        for variable in (self.page.tid, self.page.sid):
            for value in ("-1", "65536", "65537"):
                with self.subTest(field=variable, value=value):
                    variable.set(value)
                    self.page.preview()
                    self.trainer.prepare_create_box.assert_not_called()
                    self.assertIn("0～65535", self.app.status.get())
            variable.set("0")

    def test_940px_form_controls_fit_scroll_viewport(self):
        self.root.geometry("940x700")
        self.app.nb.pack(fill="both", expand=True)
        self.root.deiconify()
        self.root.update()
        canvas = self.page.tab.winfo_children()[1].winfo_children()[0]
        form = canvas.winfo_children()[0]
        right_edge = canvas.winfo_rootx() + canvas.winfo_width()

        def within_form(widget):
            parent = widget
            while parent is not self.root:
                if parent is form:
                    return True
                parent = parent.master
            return False

        controls = [widget for widget in self.page._inputs if within_form(widget)]
        self.assertGreater(len(controls), 20)
        for widget in controls:
            with self.subTest(widget=str(widget)):
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right_edge)
                self.assertLessEqual(
                    widget.winfo_rootx() + widget.winfo_width(),
                    widget.master.winfo_rootx() + widget.master.winfo_width(),
                )


if __name__ == "__main__":
    unittest.main()
