import json
import os
import tempfile
import unittest
from pathlib import Path

from tools.research_report import write_report


class ResearchReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_hardlink_to_input_is_rejected_without_input_changes(self):
        original = self.root / "source.gba"
        original.write_bytes(b"private input")
        alias = self.root / "report.json"
        os.link(original, alias)
        with self.assertRaisesRegex(ValueError, "输入"):
            write_report(alias, {"check": True}, inputs=(original,))
        self.assertEqual(original.read_bytes(), b"private input")

    def test_atomic_replacement_preserves_other_links(self):
        previous = self.root / "previous.json"
        previous.write_bytes(b"previous report")
        destination = self.root / "new.json"
        os.link(previous, destination)
        write_report(destination, {"check": True})
        self.assertEqual(previous.read_bytes(), b"previous report")
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"check": True})

    def test_failed_serialization_leaves_existing_report_and_cleans_temp(self):
        destination = self.root / "report.json"
        destination.write_bytes(b"previous report")
        with self.assertRaises(TypeError):
            write_report(destination, {"invalid": object()})
        self.assertEqual(destination.read_bytes(), b"previous report")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_input_path_and_non_json_outputs_are_rejected(self):
        original = self.root / "input.json"
        original.write_bytes(b"input")
        with self.assertRaises(ValueError):
            write_report(original, {}, inputs=(original,))
        with self.assertRaises(ValueError):
            write_report(self.root / "save.sav", {})
