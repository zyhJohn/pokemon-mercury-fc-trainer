"""Main-window integration and independent encounter heartbeat lifecycle."""
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_app
from tests.test_unown_transactions import seed
from unown_records import CAPTURE_ADDRESS, RESEARCH_ADDRESS, FINAL_ADDRESS


class Control:
    def __init__(self):
        self.active = True
        self.threads = []
        self.last_status = {"state": "active", "species": 201, "level": 35}

    def heartbeat(self):
        self.threads.append(threading.get_ident())
        return dict(self.last_status)

    def stop(self):
        self.threads.append(threading.get_ident())
        self.active = False
        self.last_status = {"state": "inactive", "reason": "stopped"}
        return dict(self.last_status)


class GameToolsAppTests(unittest.TestCase):
    def setUp(self):
        test_app.AppTests.setUp(self)
        self.app.icons_enabled = False
        self.addCleanup(self.finish_control)

    def finish_control(self):
        self.app.busy = False
        self.app.wild_control = None

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_real_unown_page_prepares_conditions_then_commits_with_final_unchanged(self):
        seed(self.mem, self.app.profile)
        self.app.nb.select(self.app.unown_page.tab)
        self.app.refresh()
        self.wait(lambda: not self.app.busy)
        with patch("unown_page.messagebox.askokcancel", return_value=True):
            self.app.unown_page.complete_draft()
        self.assertEqual(self.mem.writes, 0)
        self.app.unown_page.preview()
        self.wait(lambda: not self.app.busy)
        final = self.mem.read(FINAL_ADDRESS, 1)
        with patch("unown_page.messagebox.askokcancel", return_value=True):
            self.app.unown_page.commit()
            self.wait(lambda: not self.app.busy)
        self.assertEqual(self.mem.read(CAPTURE_ADDRESS, 4), b"\xff" * 4)
        self.assertEqual(self.mem.read(RESEARCH_ADDRESS, 1), b"\xff")
        self.assertEqual(self.mem.read(FINAL_ADDRESS, 1), final)
        self.assertFalse(self.app.unown_page.has_changes())

    def test_record_draft_discard_can_cancel_reconnect(self):
        with patch.object(self.app.unown_page, "has_changes", return_value=True), \
             patch("trainer_app.messagebox.askyesno", return_value=False):
            current = self.app.trainer
            self.app.connect()
        self.assertIs(self.app.trainer, current)
        self.assertFalse(self.app.busy)

    def test_heartbeat_runs_off_tk_and_does_not_unfreeze_an_existing_job(self):
        control = Control()
        self.app.wild_control = control
        self.app.wild_page.refresh_profile()
        self.app.busy = True
        self.app._poll_wild_heartbeat()
        self.wait(lambda: not self.app.wild_results.empty())
        self.app._poll_wild_heartbeat()
        self.assertTrue(self.app.busy)
        self.assertTrue(control.threads)
        self.assertNotIn(threading.get_ident(), control.threads)
        self.assertEqual(self.app.wild_page.active_state["species"], 201)
        self.app.busy = False

    def test_previous_connection_heartbeat_result_is_ignored(self):
        previous, current = Control(), Control()
        current.active = False
        self.app.wild_control = current
        self.app.wild_page.refresh_profile()
        self.app.wild_results.put((previous, self.app.trainer,
                                  self.app.trainer.connection_generation,
                                  {"state": "active", "species": 25, "level": 99}, None))
        self.app._poll_wild_heartbeat()
        self.assertIsNone(self.app.wild_page.active_state)

    def test_active_control_stops_and_closes_socket_on_worker_before_window_closes(self):
        control = Control()
        closed_on = []
        self.mem.close = lambda: closed_on.append(threading.get_ident())
        self.app.wild_control = control
        self.app.close()
        self.assertTrue(self.app.busy)
        self.wait(lambda: self.app.closed)
        self.assertTrue(control.threads and closed_on)
        self.assertNotIn(threading.get_ident(), control.threads + closed_on)
