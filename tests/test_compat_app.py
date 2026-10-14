import copy
import json
import struct
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from box_preferences import BoxPreferences
from rom_versions import load_profile, rom_identity
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from trainer_app import App
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER


class CompatibilityAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.icons_enabled = False
        self.addCleanup(self.app.close)
        self.app.box_preferences = BoxPreferences(Path(self.temp.name) / "locks.json")

    def compatible_profile(self, crc):
        profile = copy.deepcopy(load_profile(crc="518482f7"))
        profile["rom_crc32"] = crc
        profile["rom_identity"] = "crc32:" + crc
        profile["compatibility"] = {
            "baseline_crc32": "518482f7",
            "baseline_name": profile["name"],
            "checks": [{"name": "engine", "passed": True}],
        }
        return profile

    def memory(self, profile):
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        memory.port = 8891
        memory.connect = lambda: None
        memory.was_closed = False

        def close():
            memory.was_closed = True
            memory.capabilities = set()

        memory.close = close
        return memory

    def pump(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertFalse(self.app.busy, "background operation did not finish")

    def export(self):
        path = Path(self.temp.name) / "diagnostic.json"
        with patch("trainer_app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.export()
        return json.loads(path.read_text(encoding="utf-8"))

    def test_unknown_rejection_preserves_actual_crc_capabilities_port_and_report(self):
        profile = self.compatible_profile("a1234567")
        memory = self.memory(profile)
        failure = ValueError("关键引擎指纹不一致")
        failure.compatibility_report = {"checks": [{"name": "engine", "passed": False}]}
        with patch("trainer_app.MemClient", return_value=memory), patch(
            "trainer_app.load_profile", side_effect=failure
        ) as selector, patch("trainer_app.messagebox.showerror"):
            self.app.connect()
            self.pump()
        self.assertIs(selector.call_args.kwargs["memory"], memory)
        report = self.export()
        self.assertEqual(report["rom_crc32"], "a1234567")
        self.assertEqual(report["connection_port"], 8891)
        self.assertIn("ROMCRC", report["bridge_capabilities"])
        self.assertEqual(report["connection_diagnostic"]["stage"], "profile_selection")
        self.assertEqual(report["connection_diagnostic"]["outcome"], "failed")
        self.assertEqual(report["compatibility"], failure.compatibility_report)
        self.assertIsNone(report["rom_profile"])
        self.assertIsNone(report["layout"])
        self.assertIsNone(report["rom_identity"])
        self.assertIsNone(self.app.mem)
        self.assertTrue(memory.was_closed)
        self.assertNotIn("party", report)

    def test_backend_rejects_unverified_unknown_rom_and_exports_candidate_evidence(self):
        memory = self.memory(self.compatible_profile("a0123456"))
        with patch("trainer_app.MemClient", return_value=memory), patch(
            "trainer_app.messagebox.showerror"
        ):
            self.app.connect()
            self.pump()
        report = self.export()
        self.assertEqual(report["rom_crc32"], "a0123456")
        self.assertEqual(report["connection_diagnostic"]["outcome"], "failed")
        self.assertEqual(report["connection_diagnostic"]["stage"], "profile_selection")
        self.assertTrue(report["compatibility"]["candidates"])
        self.assertEqual(report["compatibility"]["actual_crc32"], "a0123456")
        self.assertIsNone(report["rom_profile"])
        self.assertTrue(memory.was_closed)
        self.assertEqual(memory.writes, 0)

    def test_failed_reconnect_does_not_export_previous_success_profile(self):
        known = load_profile(crc="4755f497")
        first = self.memory(known)
        self.app.run = lambda label, job, done: done(job())
        with patch("trainer_app.MemClient", return_value=first):
            self.app.connect()
        second = self.memory(self.compatible_profile("a2345678"))
        with patch("trainer_app.MemClient", return_value=second), patch(
            "trainer_app.load_profile", side_effect=ValueError("不兼容")
        ), self.assertRaisesRegex(ValueError, "不兼容"):
            self.app.connect()
        report = self.export()
        self.assertTrue(first.was_closed)
        self.assertEqual(report["rom_crc32"], "a2345678")
        self.assertIsNone(report["rom_profile"])
        self.assertIsNone(report["layout"])
        self.assertNotIn("1.2", self.root.title())
        self.assertIn("未连接", self.root.title())
        self.assertNotIn("party", report)

    def test_missing_romcrc_capability_keeps_handshake_diagnostics(self):
        memory = self.memory(load_profile(crc="b4af11c8"))
        memory.capabilities = {"BATCH"}
        self.app.run = lambda label, job, done: done(job())
        with patch("trainer_app.MemClient", return_value=memory), self.assertRaisesRegex(
            ValueError, "mercury_bridge.lua"
        ):
            self.app.connect()
        report = self.export()
        self.assertEqual(report["bridge_capabilities"], ["BATCH"])
        self.assertEqual(report["connection_port"], 8891)
        self.assertEqual(report["connection_diagnostic"]["stage"], "bridge_capabilities")
        self.assertIsNone(report["rom_crc32"])
        self.assertTrue(memory.was_closed)

    def test_compatible_title_identifies_baseline_and_exports_actual_identity(self):
        profile = self.compatible_profile("a3456789")
        memory = self.memory(profile)
        self.app.run = lambda label, job, done: done(job())
        with patch("trainer_app.MemClient", return_value=memory), patch(
            "trainer_app.load_profile", return_value=profile
        ):
            self.app.connect()
        self.assertIn("兼容基线", self.root.title())
        self.assertIn("a3456789", self.root.title())
        self.assertIn(profile["name"].split(" / ")[0], self.root.title())
        report = self.export()
        self.assertEqual(report["rom_identity"], "crc32:a3456789")
        self.assertEqual(report["rom_crc32"], "a3456789")
        self.assertEqual(report["compatibility"], profile["compatibility"])
        self.assertEqual(memory.writes, 0)

    def test_two_compatible_roms_keep_local_box_locks_separate(self):
        first_profile = self.compatible_profile("a4567890")
        second_profile = self.compatible_profile("a5678901")
        save_path = str(Path(self.temp.name) / "same-save.sav")
        self.app.run = lambda label, job, done: done(job())

        def connect(profile):
            memory = self.memory(profile)
            with patch("trainer_app.MemClient", return_value=memory), patch(
                "trainer_app.load_profile", return_value=profile
            ):
                self.app.connect()

        connect(first_profile)
        self.app.read_box()
        with patch("trainer_app.filedialog.askopenfilename", return_value=save_path):
            self.app.choose_box_save()
        self.app.box_lock.set(True)
        self.app.toggle_box_lock()
        self.assertEqual(self.app.trainer.locked_boxes, {0})
        connect(second_profile)
        self.assertEqual(self.app.trainer.locked_boxes, set())
        self.app.box_number.set("2")
        self.app.read_box()
        self.app.box_lock.set(True)
        self.app.toggle_box_lock()
        self.assertEqual(self.app.trainer.locked_boxes, {1})
        connect(first_profile)
        self.assertEqual(self.app.trainer.locked_boxes, {0})
        self.assertEqual(self.app.box_preferences.load(rom_identity(second_profile), save_path), {1})
        self.assertEqual(self.app.box_preferences.load(first_profile["rom_sha256"], save_path), set())

    def test_known_connect_preserves_exact_release_and_existing_box_identity(self):
        self.app.run = lambda label, job, done: done(job())
        save_path = str(Path(self.temp.name) / "known-save.sav")
        self.app.box_save_path.set(save_path)
        for crc in ("b4af11c8", "4755f497", "518482f7"):
            with self.subTest(crc=crc):
                profile = load_profile(crc=crc)
                self.app.box_preferences.save(profile["rom_sha256"], save_path, {2})
                memory = self.memory(profile)
                old_rtc = self.app.rtc_profile
                with patch("trainer_app.MemClient", return_value=memory):
                    self.app.connect()
                self.assertEqual(self.app.profile["rom_crc32"], crc)
                self.assertNotIn("兼容基线", self.root.title())
                self.assertIs(self.app.rtc_profile, old_rtc)
                self.assertEqual(rom_identity(profile), profile["rom_sha256"])
                self.assertEqual(self.app.trainer.locked_boxes, {2})
                self.assertEqual(self.app.connection_diagnostic["outcome"], "success")
                self.assertEqual(memory.writes, 0)

    def test_compatibility_runs_off_thread_and_close_waits_for_main_thread_display(self):
        profile = self.compatible_profile("a6789012")
        memory = self.memory(profile)
        release = threading.Event()
        started = threading.Event()
        main_thread = threading.get_ident()
        threads = []

        def select_profile(*args, **kwargs):
            threads.append(("compatibility", threading.get_ident()))
            self.assertIs(kwargs["memory"], memory)
            started.set()
            release.wait(3)
            return profile

        original_apply = self.app.apply_snapshot

        def display(snap):
            threads.append(("display", threading.get_ident()))
            return original_apply(snap)

        self.app.apply_snapshot = display
        with patch("trainer_app.MemClient", return_value=memory), patch(
            "trainer_app.load_profile", side_effect=select_profile
        ):
            self.app.connect()
            self.assertTrue(started.wait(2))
            self.app.close()
            self.assertTrue(self.app.close_requested)
            self.assertFalse(self.app.closed)
            release.set()
            self.pump()
        self.assertTrue(self.app.closed)
        self.assertTrue(memory.was_closed)
        self.assertNotEqual(threads[0][1], main_thread)
        self.assertEqual(threads[1], ("display", main_thread))

    def test_local_rtc_uses_rom_bytes_for_compatible_profile_selection(self):
        profile = self.compatible_profile("a7890123")
        rom_path = Path(self.temp.name) / "test.gba"
        rom_path.write_bytes(b"local-ROM-test")
        self.app.rtc_rom_path.set(str(rom_path))
        self.app.rtc_save_path.set(str(Path(self.temp.name) / "test.sav"))
        self.app.run = lambda label, job, done: done(job())
        self.app.apply_saved_time = lambda snapshot: None
        with patch("trainer_app.load_profile", return_value=profile) as selector, patch(
            "trainer_app.read_save", return_value={}
        ):
            self.app.read_saved_time()
        self.assertEqual(selector.call_args.args, (b"local-ROM-test",))
        self.assertIn("root", selector.call_args.kwargs)
        self.assertIs(self.app.rtc_profile, profile)


if __name__ == "__main__":
    unittest.main()
