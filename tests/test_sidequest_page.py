import json
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tkinter import ttk

from sidequest_page import SidequestPage


class SidequestPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.layout = Path(self.temp.name) / "layout.json"
        self.layout.write_text(json.dumps({"schema": 1}), encoding="utf-8")
        self.app = SimpleNamespace(nb=ttk.Notebook(self.root), trainer=None,
                                   status=tk.StringVar())
        self.app.button = lambda parent, text, command, **pack: ttk.Button(
            parent, text=text, command=command)
        self.rows = [{"id": f"{i:03d}", "title": title, "summary": summary,
                      "source_url": f"https://sum-light.github.io/azoth-wiki/sidequests/{i:03d}/",
                      "rewards": [], "locations": [], "stages": [], "notes": []}
                     for i, title, summary in ((1, "失落的礼物", "寻找礼物"),
                                              (2, "火箭队", "查明原因"),
                                              (3, "海边调查", "寻找石头"),
                                              (4, "未读取任务", "调查"))]
        self.page = SidequestPage(self.app, self.rows, self.layout)

    def test_initially_unknown_is_not_counted_as_incomplete(self):
        self.assertEqual(len(self.page.trees["all"].get_children()), 4)
        self.assertEqual(self.page.trees["incomplete"].get_children(), ())
        self.assertEqual(self.page.trees["all"].item("001", "values")[1:3],
                         ("—", "未读取"))
        self.assertIn("未读取 4", self.page.count.get())

    def test_status_tabs_marks_and_search_are_consistent(self):
        self.page.statuses = {"001": "complete", "002": "incomplete",
                              "003": "in_progress"}
        self.page.render()
        self.assertEqual(self.page.trees["complete"].get_children(), ("001",))
        self.assertEqual(self.page.trees["incomplete"].get_children(), ("002", "003"))
        self.assertEqual(self.page.trees["in_progress"].get_children(), ("003",))
        self.assertEqual(self.page.trees["all"].item("001", "values")[1], "✓")
        self.assertEqual(self.page.trees["all"].item("003", "values")[1], "✗")
        self.page.query.set("石头")
        self.assertEqual(self.page.trees["all"].get_children(), ("003",))
        self.assertIn("支线总数 4", self.page.count.get())
        self.page.invalidate()
        self.assertEqual(self.page.trees["in_progress"].get_children(), ())

    def test_read_worker_is_read_only_and_old_connection_result_is_dropped(self):
        calls = []
        trainer = SimpleNamespace(g=object(), profile={}, verify=lambda: calls.append("verify"))
        self.app.trainer = trainer
        jobs = []
        self.app.run = lambda label, job, done: jobs.append((job, done))
        self.page.read()
        result = []
        with patch("sidequest_page.read_snapshot", return_value={"001": "complete"}) as read:
            worker = threading.Thread(target=lambda: result.append(jobs[0][0]()))
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            read.assert_called_once_with(trainer.g, trainer.profile, layout={"schema": 1})
        self.assertEqual(calls, ["verify"])
        self.assertEqual(self.page.statuses, {})
        self.app.trainer = object()
        jobs[0][1](result[0])
        self.assertEqual(self.page.statuses, {})
        self.app.trainer = trainer
        jobs[0][1](result[0])
        self.assertEqual(self.page.statuses, {"001": "complete"})

    def test_selected_detail_opens_correct_source(self):
        tree = self.page.trees["all"]
        tree.selection_set("002")
        self.page.show_detail(tree)
        self.assertIn("火箭队", self.page.detail.get("1.0", "end"))
        with patch("sidequest_page.webbrowser.open") as opened:
            self.page.open_selected()
        opened.assert_called_once_with(self.rows[1]["source_url"])


if __name__ == "__main__":
    unittest.main()
