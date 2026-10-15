import copy
import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

from unown_page import UnownPage
from unown_records import FINAL_ADDRESS, LAYOUT


class StubTrainer:
    def __init__(self):
        self.connection_generation = "connection-one"
        self.calls = []
        self.snapshot = {"task_accepted": True, "task_complete": False, "fields": [
            {"key": "captured_A", "label": "A", "group": "captured", "value": False,
             "editable": True},
            {"key": "captured_B", "label": "B", "group": "captured", "value": True,
             "editable": True},
            {"key": "note_order", "label": "调查顺序", "group": "research", "value": False,
             "editable": True, "description": "改变研究阶段"},
            {"key": "note_final", "label": "最终记录", "group": "final", "value": False,
             "editable": False, "description": "关联任务和奖励，只读"}]}

    def snapshot_unown_records(self):
        self.calls.append(("read", threading.get_ident()))
        return copy.deepcopy(self.snapshot)

    def prepare_unown_records(self, snapshot, changes):
        self.calls.append(("prepare", threading.get_ident(), changes))
        return {"changes": dict(changes)}

    def commit_unown_records(self, prepared):
        self.calls.append(("commit", threading.get_ident(), prepared))
        for field in self.snapshot["fields"]:
            if field["key"] in prepared["changes"]:
                field["value"] = prepared["changes"][field["key"]]
        return {"ok": True}


class UnownPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.trainer = StubTrainer()
        self.jobs = []
        self.app = SimpleNamespace(root=self.root, nb=ttk.Notebook(self.root),
                                   trainer=self.trainer, busy=False, closed=False,
                                   close_requested=False, status=tk.StringVar())

        def button(parent, text, command, **pack):
            widget = ttk.Button(parent, text=text, command=command)
            widget.pack(**pack)
            return widget

        self.app.button = button
        self.app.run = lambda label, job, done: self.jobs.append((job, done))
        self.page = UnownPage(self.app)
        self.main_thread = threading.get_ident()

    def finish(self, index=-1):
        job, done = self.jobs[index]
        result, error = [], []

        def worker():
            try:
                result.append(job())
            except Exception as exc:
                error.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "worker accessed Tk or did not finish")
        if error:
            raise error[0]
        done(result[0])

    def read(self):
        self.page.read()
        self.finish()

    def complete(self):
        with patch("unown_page.messagebox.askokcancel", return_value=True):
            self.page.complete_draft()

    def test_initial_controls_and_read_only_loading(self):
        self.assertIn("disabled", self.page.all_button.state())
        self.assertIn("disabled", self.page.preview_button.state())
        self.assertIn("disabled", self.page.commit_button.state())
        self.read()
        self.assertEqual([call[0] for call in self.trainer.calls], ["read"])
        self.assertNotEqual(self.trainer.calls[0][1], self.main_thread)
        self.assertEqual(len(self.page.values), 3)
        self.assertNotIn("research", self.page.groups)
        self.assertNotIn("note_order", self.page.values)
        self.assertNotIn("note_order", self.page.checkbuttons)
        self.assertIn("disabled", self.page.checkbuttons["note_final"].state())
        self.assertFalse(self.page.has_changes())

    def test_group_select_clear_never_changes_research_or_read_only_record(self):
        self.read()
        self.page.pages.select(0)
        self.page.select_group(True)
        self.assertEqual(self.page.changes(), {"captured_A": True})
        self.assertEqual(self.page.completion_overrides, {})
        self.page.select_group(False)
        self.assertEqual(self.page.changes(), {"captured_B": False})
        self.page.pages.select(1)
        self.page.select_group(True)
        self.assertFalse(self.page.values["note_final"].get())

    def test_preview_contains_specific_changes_and_edit_invalidates(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.page.preview()
        self.finish()
        self.assertIsNotNone(self.page.prepared)
        self.assertIn("A：未记录 → 已记录", self.page.detail.get("1.0", "end"))
        self.assertEqual(self.trainer.calls[-1][2], {"captured_A": True})
        self.assertNotEqual(self.trainer.calls[-1][1], self.main_thread)
        self.page.values["captured_B"].set(False)
        self.assertIsNone(self.page.prepared)
        self.assertIn("disabled", self.page.commit_button.state())

    def test_cancel_confirmation_performs_zero_writes(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.page.preview()
        self.finish()
        jobs = len(self.jobs)
        with patch("unown_page.messagebox.askokcancel", return_value=False) as ask:
            self.page.commit()
        self.assertIn("A：未记录 → 已记录", ask.call_args.args[1])
        self.assertEqual(len(self.jobs), jobs)
        self.assertNotIn("commit", [call[0] for call in self.trainer.calls])

    def test_commit_rereads_and_all_worker_calls_avoid_tk(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.page.preview()
        self.finish()
        with patch("unown_page.messagebox.askokcancel", return_value=True):
            self.page.commit()
        self.finish()
        self.assertEqual([call[0] for call in self.trainer.calls],
                         ["read", "prepare", "commit", "read"])
        self.assertTrue(all(call[1] != self.main_thread for call in self.trainer.calls))
        self.assertFalse(self.page.has_changes())
        self.assertIsNone(self.page.prepared)

    def test_old_connection_generation_and_closed_results_are_dropped(self):
        self.page.read()
        self.trainer.connection_generation = "connection-two"
        self.finish()
        self.assertIsNone(self.page.snapshot)
        self.page.read()
        self.app.trainer = StubTrainer()
        self.finish()
        self.assertIsNone(self.page.snapshot)
        self.app.trainer = self.trainer
        self.page.read()
        self.app.closed = True
        self.finish()
        self.assertIsNone(self.page.snapshot)

    def test_invalidation_rejects_pending_preview(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.page.preview()
        self.page.invalidate()
        self.finish()
        self.assertIsNone(self.page.prepared)
        self.assertIsNone(self.page.snapshot)

    def test_loaded_snapshot_becomes_unusable_after_connection_generation_changes(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.trainer.connection_generation = "connection-two"
        self.page.update_actions()
        self.assertIn("disabled", self.page.preview_button.state())
        jobs = len(self.jobs)
        self.page.preview()
        self.page.select_group(False)
        self.assertEqual(len(self.jobs), jobs)
        self.assertTrue(self.page.values["captured_A"].get())

    def test_cancel_read_keeps_unsaved_draft(self):
        self.read()
        self.page.values["captured_A"].set(True)
        jobs = len(self.jobs)
        with patch("unown_page.messagebox.askokcancel", return_value=False):
            self.page.read()
        self.assertEqual(len(self.jobs), jobs)
        self.assertEqual(self.page.changes(), {"captured_A": True})

    def test_failed_read_discards_stale_snapshot_and_preview(self):
        self.read()
        self.page.values["captured_A"].set(True)
        self.page.preview()
        self.finish()

        def fail():
            raise ValueError("读取失败")

        self.trainer.snapshot_unown_records = fail
        with patch("unown_page.messagebox.askokcancel", return_value=True):
            self.page.read()
        with self.assertRaisesRegex(ValueError, "读取失败"):
            self.finish()
        self.assertIsNone(self.page.snapshot)
        self.assertIsNone(self.page.prepared)
        self.assertIn("disabled", self.page.commit_button.state())

    def test_empty_changes_and_busy_freeze_are_safe(self):
        self.read()
        jobs = len(self.jobs)
        self.page.preview()
        self.assertEqual(len(self.jobs), jobs)
        self.assertIn("没有变更", self.app.status.get())
        self.page.freeze()
        self.assertTrue(all("disabled" in button.state()
                            for button in self.page.checkbuttons.values()))
        self.page.select_group(True)
        self.assertFalse(self.page.has_changes())
        self.page.thaw()
        self.assertNotIn("disabled", self.page.checkbuttons["captured_A"].state())
        self.assertIn("disabled", self.page.checkbuttons["note_final"].state())

    def test_completion_button_only_prepares_draft_and_respects_current_edits(self):
        self.read()
        self.page.values["captured_B"].set(False)
        jobs = len(self.jobs)
        with patch("unown_page.unown_records.completion_changes", create=True,
                   return_value={"captured_A": True, "captured_B": True,
                                 "note_order": True}) as helper:
            self.complete()
        visible = {field["key"]: field["value"] for field in helper.call_args.args[0]["fields"]}
        self.assertFalse(visible["captured_B"])
        self.assertEqual(self.page.changes(), {"captured_A": True, "note_order": True})
        self.assertFalse(self.page.values["note_final"].get())
        self.assertIsNone(self.page.prepared)
        self.assertEqual(len(self.jobs), jobs)
        self.assertEqual([call[0] for call in self.trainer.calls], ["read"])
        self.assertIn("研究员", self.page.detail.get("1.0", "end"))

    def test_completion_warns_every_time_and_cancel_preserves_draft_and_preview(self):
        self.read()
        with patch("unown_page.unown_records.completion_changes", return_value={
                "captured_A": True, "note_order": True}):
            self.complete()
        self.page.preview()
        self.finish()
        changes = self.page.changes()
        overrides = dict(self.page.completion_overrides)
        prepared = self.page.prepared
        revision = self.page.revision
        jobs = len(self.jobs)
        with patch("unown_page.messagebox.askokcancel", return_value=False) as warning, patch(
                "unown_page.unown_records.completion_changes") as helper:
            self.page.complete_draft()
            self.page.complete_draft()
        self.assertEqual(warning.call_count, 2)
        helper.assert_not_called()
        text = warning.call_args.args[1]
        for phrase in ("28种", "6项", "跳过正常调查阶段", "游戏内保存", ".sav", "独立副本",
                       "不能替代", "正常发奖", "不重置已完成"):
            self.assertIn(phrase, text)
        self.assertEqual(self.page.changes(), changes)
        self.assertEqual(self.page.completion_overrides, overrides)
        self.assertIs(self.page.prepared, prepared)
        self.assertEqual(self.page.revision, revision)
        self.assertEqual(len(self.jobs), jobs)
        self.assertNotIn("commit", [call[0] for call in self.trainer.calls])

    def test_hidden_research_is_explicit_in_preview_and_final_confirmation(self):
        self.read()
        with patch("unown_page.unown_records.completion_changes", return_value={
                "captured_A": True, "note_order": True}):
            self.complete()
        self.assertEqual(self.page.completion_overrides, {"note_order": True})
        self.page.values["captured_A"].set(False)
        self.assertIn("已保留经确认的 1 项", self.page.detail.get("1.0", "end"))
        self.page.preview()
        self.finish()
        self.assertEqual(self.trainer.calls[-1][2], {"note_order": True})
        detail = self.page.detail.get("1.0", "end")
        self.assertIn("调查顺序：未记录 → 已记录", detail)
        self.assertIn("跳过正常调查阶段", detail)
        with patch("unown_page.messagebox.askokcancel", return_value=False) as confirm:
            self.page.commit()
        self.assertIn("调查顺序：未记录 → 已记录", confirm.call_args.args[1])
        self.assertIn("跳过正常调查阶段", confirm.call_args.args[1])
        self.assertNotIn("commit", [call[0] for call in self.trainer.calls])
        self.page.invalidate()
        self.assertEqual(self.page.completion_overrides, {})
        self.assertFalse(self.page.has_changes())

    def test_completion_rejects_unaccepted_and_complete_without_writes(self):
        for accepted, complete, reason in ((False, False, "尚未接取任务"),
                                            (True, True, "任务已完成，不重置奖励")):
            with self.subTest(accepted=accepted, complete=complete):
                self.trainer.snapshot.update(task_accepted=accepted, task_complete=complete)
                self.read()
                jobs = len(self.jobs)
                with patch("unown_page.unown_records.completion_changes", create=True,
                           side_effect=ValueError(reason)):
                    self.complete()
                self.assertIn(reason, self.app.status.get())
                self.assertFalse(self.page.has_changes())
                self.assertEqual(len(self.jobs), jobs)
        self.assertNotIn("commit", [call[0] for call in self.trainer.calls])

    def test_real_completion_helper_sets_34_fields_and_rejects_reward_reset(self):
        fields = [{"key": "captured_" + letter, "label": letter, "group": "captured",
                   "editable": True, "value": False} for letter in LAYOUT["letters"]]
        fields += [{**note, "group": "research" if note["editable"] else "final", "value": False}
                   for note in LAYOUT["notes"]]
        snapshot = {"rom_sha256": next(iter(LAYOUT["releases"])), "fields": fields,
                    "records": {FINAL_ADDRESS: b"\x02"},
                    "task_accepted": True, "task_complete": False}
        self.trainer.snapshot = snapshot
        self.read()
        self.assertEqual(self.page.groups, ["captured", "final"])
        self.assertEqual(len(self.page.checkbuttons), 29)
        self.assertFalse(any(field["group"] == "research" for field in self.page.fields.values()))
        self.page.pages.select(0)
        self.page.select_group(True)
        self.assertEqual(len(self.page.changes()), 28)
        self.assertTrue(all(key.startswith("captured_") for key in self.page.changes()))
        self.complete()
        self.assertEqual(len(self.page.changes()), 34)
        self.assertEqual(len(self.page.completion_overrides), 6)
        self.assertNotIn("note_final", self.page.changes())
        self.assertEqual([call[0] for call in self.trainer.calls], ["read"])
        self.page.preview()
        self.finish()
        detail = self.page.detail.get("1.0", "end")
        for note in LAYOUT["notes"]:
            if note["editable"]:
                self.assertIn(note["label"], detail)
        self.assertEqual(len(self.trainer.calls[-1][2]), 34)
        self.page.invalidate()
        self.trainer.snapshot["records"][FINAL_ADDRESS] = b"\x06"
        self.trainer.snapshot["task_complete"] = True
        self.read()
        self.complete()
        self.assertIn("已完成", self.app.status.get())
        self.assertFalse(self.page.has_changes())
        self.page.invalidate()
        self.trainer.snapshot["records"][FINAL_ADDRESS] = b"\x00"
        self.trainer.snapshot["task_accepted"] = False
        self.trainer.snapshot["task_complete"] = False
        self.read()
        self.complete()
        self.assertIn("接取", self.app.status.get())
        self.assertFalse(self.page.has_changes())
        self.assertEqual([call[0] for call in self.trainer.calls], ["read", "prepare", "read", "read"])


if __name__ == "__main__":
    unittest.main()
