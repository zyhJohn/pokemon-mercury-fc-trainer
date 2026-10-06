import json
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import Mock, patch

from box_data import BoxPokemon
from distribution_catalog import load_distributions
from gift_page import GiftPage


class GiftPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.profile = json.loads(Path("rom_profile_v12.json").read_text("utf-8"))
        self.catalog = load_distributions()
        self.row = next(row for row in self.catalog["rows"]
                        if row["id"].startswith("mercury-home-") and row["compatibility"] == "verified")
        self.mon = BoxPokemon(bytes.fromhex(self.row["template"]["native_pc_hex"]))
        self.trainer = SimpleNamespace(profile=self.profile,
            prepare_gift_box=Mock(return_value={"pokemon": self.mon, "destination": (0, 0)}),
            commit_gift_box=Mock(return_value={"changed": True}),
            snapshot_box=Mock(return_value={"index": 0}))
        self.app = SimpleNamespace(root=self.root, nb=ttk.Notebook(self.root),
                                  trainer=self.trainer, busy=False,
                                  names=json.loads(Path("names.json").read_text("utf-8")),
                                  status=tk.StringVar(), discard_box_changes=lambda: True,
                                  discard_party_changes=lambda: True,
                                  apply_box_snapshot=Mock())
        self.app.button = lambda parent, text, command, **pack: ttk.Button(parent, text=text, command=command)
        self.app.run = lambda label, job, done: done(job())
        self.page = GiftPage(self.app, self.catalog)
        self.page.tree.selection_set(self.row["id"])
        self.page.show_detail()

    def test_native_source_is_visible_and_key_is_usable(self):
        self.assertIn("TID", self.page.detail.get("1.0", "end"))
        self.assertNotIn("disabled", self.page.deposit_button.state())
        self.page.copy_key()
        self.assertEqual(self.root.clipboard_get(), self.row["source_fields"]["manual_key"])

    def test_cancel_preview_never_commits(self):
        with patch("gift_page.messagebox.askokcancel", return_value=False):
            self.page.deposit()
        self.trainer.prepare_gift_box.assert_called_once_with(self.row["id"], 0)
        self.trainer.commit_gift_box.assert_not_called()

    def test_confirm_commits_and_displays_readback(self):
        with patch("gift_page.messagebox.askokcancel", return_value=True):
            self.page.deposit()
        self.trainer.commit_gift_box.assert_called_once()
        self.app.apply_box_snapshot.assert_called_once_with({"index": 0})
        self.assertIn("读回核对", self.app.status.get())

    def test_changed_target_invalidates_preview_and_busy_disables_button(self):
        pending = []
        self.app.run = lambda label, job, done: pending.append((job, done))
        self.page.deposit()
        prepared = pending[0][0]()
        self.page.box.set("2")
        with patch("gift_page.messagebox.askokcancel") as confirm:
            pending[0][1](prepared)
        confirm.assert_not_called()
        self.trainer.commit_gift_box.assert_not_called()
        self.app.busy = True
        self.page.update_actions()
        self.assertIn("disabled", self.page.deposit_button.state())

    def test_party_replacement_requires_explicit_slot_and_shows_old_identity(self):
        from tests.test_pokemon_data import sample
        self.page.destination.set("顶替队伍成员")
        self.page.deposit()
        self.trainer.prepare_gift_box.assert_not_called()
        self.assertIn("明确选择", self.app.status.get())
        self.page.replacement.set("2")
        self.trainer.prepare_gift_party = Mock(return_value={
            "pokemon": self.mon, "destination": ("party", 1), "replaced": sample()})
        self.trainer.commit_gift_party = Mock()
        with patch("gift_page.messagebox.askokcancel", return_value=False) as confirm:
            self.page.deposit()
        self.trainer.prepare_gift_party.assert_called_once_with(self.row["id"], replacement_slot=1)
        self.assertIn("将顶替", confirm.call_args.args[1])
        self.trainer.commit_gift_party.assert_not_called()


if __name__ == "__main__":
    unittest.main()
