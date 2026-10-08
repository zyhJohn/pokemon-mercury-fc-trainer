import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from field_page import RepelWindow


class RepelWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.snap = {"steps": 85}
        self.prepared = {"steps": 85, "new_steps": 0}
        self.trainer = SimpleNamespace(
            snapshot_repel=Mock(return_value=self.snap),
            prepare_repel_steps=Mock(return_value=self.prepared),
            commit_repel_steps=Mock(return_value={"changed": True, "backup": "backup.json"}),
        )
        self.app = SimpleNamespace(root=self.root, trainer=self.trainer,
                                   busy=False, status=tk.StringVar())
        self.jobs = []
        self.app.run = lambda label, job, done: self.jobs.append((job, done))
        self.page = RepelWindow(self.app)
        self.page.open()
        self.jobs.pop(0)[1](self.snap)

    def test_zero_preview_commits_only_after_confirmation_and_shows_backup(self):
        self.page.preview(0)
        job, done = self.jobs.pop(0)
        self.assertEqual(job(), self.prepared)
        with patch("field_page.messagebox.askokcancel", return_value=True):
            done(self.prepared)
        job, done = self.jobs.pop(0)
        self.trainer.snapshot_repel.return_value = {"steps": 0}
        done(job())
        self.trainer.commit_repel_steps.assert_called_once_with(self.prepared)
        self.assertIn("backup.json", self.page.detail.get())
        self.assertEqual(self.page.steps.get(), "0")

    def test_input_and_connection_changes_cancel_pending_preview(self):
        self.page.preview(0)
        _, done = self.jobs.pop(0)
        self.page.steps.set("1")
        with patch("field_page.messagebox.askokcancel") as confirm:
            done(self.prepared)
        confirm.assert_not_called()
        self.page.preview(0)
        _, done = self.jobs.pop(0)
        self.app.trainer = object()
        with patch("field_page.messagebox.askokcancel") as confirm:
            done(self.prepared)
        confirm.assert_not_called()
        self.trainer.commit_repel_steps.assert_not_called()

    def test_range_is_checked_before_worker(self):
        self.page.steps.set("251")
        with patch("field_page.messagebox.showerror") as error:
            self.page.preview()
        error.assert_called_once()
        self.assertEqual(self.jobs, [])


if __name__ == "__main__":
    unittest.main()
