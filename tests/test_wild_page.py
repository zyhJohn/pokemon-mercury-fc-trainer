import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

from wild_encounter_page import WildEncounterPage


class StubControl:
    def __init__(self):
        self.calls = []
        self.result = {"state": "inactive", "species": None, "level": None, "reason": "", "nonce": "one"}

    def arm(self, species, level):
        self.calls.append(("arm", threading.get_ident(), species, level))
        self.result = {"state": "active", "species": species, "level": level, "reason": "", "nonce": "one"}
        return dict(self.result)

    def stop(self):
        self.calls.append(("stop", threading.get_ident()))
        self.result = {"state": "inactive", "species": None, "level": None, "reason": "", "nonce": "one"}
        return dict(self.result)

    def status(self):
        self.calls.append(("status", threading.get_ident()))
        return dict(self.result)


class WildPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.control = StubControl()
        self.trainer = SimpleNamespace(connection_generation="one", profile={"species": {"1": {}, "25": {}, "1500": {}}})
        self.jobs = []
        self.app = SimpleNamespace(root=self.root, nb=ttk.Notebook(self.root), trainer=self.trainer,
                                   wild_control=self.control, names={"breeds": {"1": "妙蛙种子", "25": "皮卡丘", "1500": "皮卡丘形态"}},
                                   busy=False, closed=False, close_requested=False, status=tk.StringVar())

        def button(parent, text, command, **pack):
            widget = ttk.Button(parent, text=text, command=command)
            widget.pack(**pack)
            return widget

        self.app.button = button
        self.app.run = lambda label, job, done: self.jobs.append((job, done))
        self.page = WildEncounterPage(self.app)
        self.main_thread = threading.get_ident()
        self.page.species.set(self.label(25))

    def label(self, species):
        return next(label for label, ident in self.page.species_by_label.items() if ident == species)

    def finish(self, index=-1, before_done=None):
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
        if before_done:
            before_done()
        done(result[0])

    def arm(self):
        with patch("wild_encounter_page.messagebox.askokcancel", return_value=True):
            self.page.arm()
        self.finish()

    def test_profile_holes_and_search_by_chinese_and_internal_id(self):
        self.assertEqual(set(self.page.species_by_label.values()), {1, 25, 1500})
        self.page.search.set("皮卡丘")
        self.assertEqual(len(self.page.species_combo.cget("values")), 2)
        self.page.search.set("1500")
        self.assertEqual(self.page.species_combo.cget("values"), (self.label(1500),))
        self.assertIn("内部编号", self.label(1500))
        self.assertEqual(self.page.species.get(), self.label(25))

    def test_invalid_species_holes_and_levels_never_enqueue_or_confirm(self):
        with patch("wild_encounter_page.messagebox.showerror") as error, patch(
                "wild_encounter_page.messagebox.askokcancel") as confirm:
            for bad in ("2", "26", "皮卡丘", "皮卡丘（内部编号 26）"):
                self.page.species.set(bad)
                self.page.arm()
            self.page.species.set(self.label(25))
            for bad in ("0", "101", "-1", "1.5", "", "五", "１２", "1e2"):
                self.page.level.set(bad)
                self.page.arm()
            self.assertEqual(error.call_count, 12)
            confirm.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_confirmation_has_species_level_scope_and_cancel_is_noop(self):
        self.page.level.set("73")
        with patch("wild_encounter_page.messagebox.askokcancel", return_value=False) as confirm:
            self.page.arm()
        text = confirm.call_args.args[1]
        for expected in ("皮卡丘", "内部编号 25", "73", "后续", "钓鱼", "训练师", "剧情", "喷雾"):
            self.assertIn(expected, text)
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.control.calls, [])

    def test_worker_uses_captured_parameters_active_state_stays_separate(self):
        self.page.level.set("73")
        with patch("wild_encounter_page.messagebox.askokcancel", return_value=True):
            self.page.arm()
        self.page.level.set("6")
        self.page.species.set(self.label(1))
        self.finish()
        self.assertEqual(self.control.calls[0][2:], (25, 73))
        self.assertNotEqual(self.control.calls[0][1], self.main_thread)
        self.assertIn("皮卡丘", self.page.active.get())
        self.assertIn("等级 73", self.page.active.get())
        self.assertEqual(self.page.level.get(), "6")
        self.assertEqual(self.page.species.get(), self.label(1))

    def test_read_and_explicit_stop_use_worker_and_do_not_arm(self):
        self.arm()
        self.page.read()
        self.finish()
        self.page.stop()
        self.finish()
        self.assertEqual([call[0] for call in self.control.calls], ["arm", "status", "stop"])
        self.assertTrue(all(call[1] != self.main_thread for call in self.control.calls))
        self.assertEqual(self.page.active_state["state"], "inactive")
        self.assertIn("已关闭", self.page.active.get())

    def test_busy_freeze_disconnect_disable_all_inputs(self):
        self.page.freeze()
        for widget in (self.page.search_entry, self.page.level_entry, self.page.species_combo,
                       self.page.arm_button, self.page.stop_button, self.page.read_button):
            self.assertIn("disabled", widget.state())
        self.page.arm()
        self.page.stop()
        self.page.read()
        self.assertEqual(self.jobs, [])
        self.page.thaw()
        self.assertIn("readonly", self.page.species_combo.state())
        self.app.busy = True
        self.page.update_actions()
        self.assertIn("disabled", self.page.search_entry.state())
        self.app.busy = False
        self.app.wild_control = None
        self.page.update_actions()
        self.assertIn("disabled", self.page.stop_button.state())

    def test_connection_generation_changed_before_job_prevents_action(self):
        with patch("wild_encounter_page.messagebox.askokcancel", return_value=True):
            self.page.arm()
        self.trainer.connection_generation = "two"
        self.finish()
        self.assertEqual(self.control.calls, [])
        self.assertIsNone(self.page.active_state)

    def test_late_result_dropped_after_invalidate_controller_or_trainer_changes(self):
        for mutation in (self.page.invalidate,
                         lambda: setattr(self.app, "wild_control", StubControl()),
                         lambda: setattr(self.app, "trainer", SimpleNamespace(connection_generation="one", profile=self.trainer.profile)),
                         lambda: setattr(self.app, "closed", True)):
            self.app.closed = False
            self.app.trainer = self.trainer
            self.app.wild_control = self.control
            self.page.refresh_profile()
            self.page.read()
            self.finish(before_done=mutation)
            self.assertIsNone(self.page.active_state)

    def test_error_reason_is_visible_and_status_can_recover(self):
        def fail(*_):
            raise ValueError("战斗中不能开启")

        self.control.arm = fail
        self.arm()
        self.assertEqual(self.page.active_state["state"], "error")
        self.assertIn("战斗中不能开启", self.page.info.get())
        self.assertIn("尚未确认", self.page.active.get())
        self.page.read()
        self.finish()
        self.assertEqual(self.page.active_state["state"], "inactive")

    def test_profile_switch_clears_draft_active_and_rebuilds_valid_ids(self):
        self.arm()
        self.trainer.profile = {"species": {"1": {}}}
        self.trainer.connection_generation = "two"
        self.page.refresh_profile()
        self.assertEqual(self.page.species.get(), "")
        self.assertIsNone(self.page.active_state)
        self.assertEqual(set(self.page.species_by_label.values()), {1})
        self.assertIn("未读取", self.page.active.get())


if __name__ == "__main__":
    unittest.main()
