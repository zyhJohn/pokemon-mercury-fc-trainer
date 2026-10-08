import tkinter as tk
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

from daycare_page import DaycareWindow


@dataclass(frozen=True)
class Parent:
    species: int = 36
    nickname: str = "皮可西"
    gender: str = "雌性"
    ot_name: str = "小智"
    otid: int = 12345
    steps: int = 5502
    egg: bool = False


class DaycareWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.snap = {"parents": (Parent(), Parent(species=132, nickname="百变怪",
                                                 gender="无性别")),
                     "eligible": True, "reason": None, "pending": False,
                     "offspring_token": 0, "compatibility_score": 20,
                     "connection_generation": "one", "before": b"\0"}
        self.prepared = {**self.snap, "after": b"@", "new_pending": True}
        self.trainer = SimpleNamespace(
            g=SimpleNamespace(capabilities={"BATCH", "ROMCRC", "CRCBATCH", "BATCHVERIFY"}),
            snapshot_daycare=Mock(return_value=self.snap),
            prepare_daycare_egg=Mock(return_value=self.prepared),
            commit_daycare_egg=Mock(return_value={"changed": True, "backup": "daycare.json"}),
        )
        self.app = SimpleNamespace(root=self.root, trainer=self.trainer, busy=False,
                                   names={"breeds": {"36": "皮可西", "132": "百变怪"}},
                                   status=tk.StringVar())
        self.jobs = []
        self.app.run = lambda label, job, done: self.jobs.append((job, done))
        self.page = DaycareWindow(self.app)
        self.page.open()
        job, done = self.jobs.pop(0)
        done(job())

    def test_parents_and_eligibility_are_visible(self):
        detail = self.page.detail.get()
        self.assertIn("皮可西", detail)
        self.assertIn("雌性", detail)
        self.assertIn("OT 小智", detail)
        self.assertIn("兼容分：20", detail)
        self.assertNotIn("disabled", self.page.action_button.state())

    def test_all_refusal_states_keep_preview_disabled(self):
        cases = [
            ("无父母", {"parents": (), "reason": "没有寄放父母"}),
            ("单父母", {"parents": (Parent(),), "reason": "只寄放一只"}),
            ("不兼容", {"compatibility_score": 0, "reason": "父母不兼容"}),
            ("已有待领", {"pending": True, "reason": "已有待领蛋"}),
            ("父母是蛋", {"parents": (Parent(egg=True), Parent()), "reason": "父母记录是蛋"}),
            ("token非零", {"offspring_token": 9, "reason": "旧后代 token 非零"}),
        ]
        for label, changes in cases:
            with self.subTest(label=label):
                snap = {**self.snap, **changes, "eligible": False}
                self.page.snapshot = snap
                self.page._render(snap)
                self.assertIn("disabled", self.page.action_button.state())
                self.assertIn(changes["reason"], self.page.detail.get())
                self.page.preview()
                self.trainer.prepare_daycare_egg.assert_not_called()

    def test_read_error_removes_previous_permission(self):
        self.trainer.snapshot_daycare.side_effect = ValueError("ROM未核验")
        self.page.read()
        job, done = self.jobs.pop(0)
        done(job())
        self.assertIn("ROM未核验", self.page.detail.get())
        self.assertIn("disabled", self.page.action_button.state())
        self.assertIsNone(self.page.snapshot)

    def test_old_bridge_is_read_only_even_when_parents_eligible(self):
        self.trainer.g.capabilities.remove("BATCHVERIFY")
        self.page._render(self.snap)
        self.assertIn("disabled", self.page.action_button.state())
        self.assertIn("桥接", self.page.detail.get())
        self.page.preview()
        self.assertEqual(self.jobs, [])

    def test_cancel_never_commits(self):
        self.page.preview()
        job, done = self.jobs.pop(0)
        with patch("daycare_page.messagebox.askokcancel", return_value=False):
            done(job())
        self.trainer.prepare_daycare_egg.assert_called_once_with()
        self.trainer.commit_daycare_egg.assert_not_called()
        self.assertNotIn("disabled", self.page.action_button.state())

    def test_confirmation_commits_once_and_shows_backup(self):
        self.page.preview()
        job, done = self.jobs.pop(0)
        with patch("daycare_page.messagebox.askokcancel", return_value=True) as confirm:
            done(job())
        self.assertIn("NPC", confirm.call_args.args[1])
        self.trainer.snapshot_daycare.return_value = {**self.snap, "eligible": False,
                                                      "pending": True, "reason": "已有待领蛋"}
        job, done = self.jobs.pop(0)
        done(job())
        self.trainer.commit_daycare_egg.assert_called_once_with(self.prepared)
        self.assertIn("daycare.json", self.page.detail.get())
        self.assertIn("disabled", self.page.action_button.state())

    def test_changed_data_connection_busy_or_closed_window_drops_preview(self):
        self.page.preview()
        job, done = self.jobs.pop(0)
        self.trainer.snapshot_daycare.return_value = {**self.snap, "step_counter": 1}
        with self.assertRaises(ValueError):
            job()
        self.trainer.snapshot_daycare.return_value = self.snap
        self.app.trainer = object()
        with patch("daycare_page.messagebox.askokcancel") as confirm:
            done(self.prepared)
        confirm.assert_not_called()
        self.app.trainer = self.trainer
        self.page.window.destroy()
        with patch("daycare_page.messagebox.askokcancel") as confirm:
            done(self.prepared)
        confirm.assert_not_called()
        self.app.busy = True
        self.page.preview()
        self.assertEqual(self.jobs, [])

    def test_closed_then_reopened_window_rejects_old_result(self):
        self.page.preview()
        _, old_done = self.jobs.pop(0)
        self.page.window.destroy()
        self.page.open()
        self.jobs.clear()
        with patch("daycare_page.messagebox.askokcancel") as confirm:
            old_done(self.prepared)
        confirm.assert_not_called()
        self.trainer.commit_daycare_egg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
