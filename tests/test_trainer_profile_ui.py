import tkinter as tk
import unittest
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trainer_app import App


class TrainerProfileUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = App.__new__(App)
        self.app.root = self.root
        self.app.nb = ttk.Notebook(self.root)
        self.app.buttons = []
        self.app.busy = False
        self.app.status = tk.StringVar()
        self.app.trainer_snapshot = None
        self.app.rival_snapshot = None
        self.app.profile_preview = None
        self.app.profile_preview_inputs = None
        self.jobs = []
        self.app.run = lambda label, job, done: self.jobs.append((job, done))
        self.app._build_trainer()
        self.player = {"name": "小明", "tid": 12, "sid": 34,
                       "gender": 0, "name_raw": "000000", "raw": b"x" * 14}
        self.rival = {"name": "小兰", "raw": b"y" * 8}
        self.prepared = {"player": self.player, "rival": self.rival,
                         "tid": "13", "sid": "34"}
        self.trainer = SimpleNamespace(
            snapshot_trainer=Mock(return_value=self.player),
            snapshot_rival=Mock(return_value=self.rival),
            prepare_player_rival=Mock(return_value=self.prepared),
            commit_player_rival=Mock(return_value={"changed": True, "backup": "one.json"}),
        )
        self.app.trainer = self.trainer
        self.app.apply_trainer_snapshot(self.player)
        self.app.apply_rival_snapshot(self.rival)

    def test_one_preview_and_one_commit_for_player_and_rival(self):
        self.app.player_tid.set("13")
        self.app.rival_name.set("小李")
        self.app.preview_trainer_profile()
        job, done = self.jobs.pop(0)
        done(job())
        self.trainer.prepare_player_rival.assert_called_once_with("13", "34", None, "小李")
        self.assertIs(self.app.profile_preview, self.prepared)
        with patch("trainer_app.messagebox.askokcancel", return_value=True):
            self.app.write_trainer_ids()
        job, done = self.jobs.pop(0)
        self.trainer.snapshot_trainer.return_value = {**self.player, "tid": 13}
        self.trainer.snapshot_rival.return_value = {**self.rival, "name": "小李"}
        done(job())
        self.trainer.commit_player_rival.assert_called_once_with(self.prepared)
        self.assertIn("one.json", self.app.status.get())
        self.assertEqual(self.app.rival_name.get(), "小李")

    def test_edit_or_reconnect_invalidates_preview(self):
        self.app.player_tid.set("13")
        self.app.preview_trainer_profile()
        job, done = self.jobs.pop(0)
        job()
        self.app.player_tid.set("14")
        done(self.prepared)
        self.assertIsNone(self.app.profile_preview)
        self.app.preview_trainer_profile()
        job, done = self.jobs.pop(0)
        self.app.trainer = object()
        done(self.prepared)
        self.assertIsNone(self.app.profile_preview)
        self.trainer.commit_player_rival.assert_not_called()


if __name__ == "__main__":
    unittest.main()
