"""Synthetic checks for native research CLI; no emulator, ROM, or save fixtures."""

import copy
import io
import json
import os
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.verify_builtin_native import (
    BATTERY_SIZE,
    _load_core,
    _save_validation,
    battery_bytes,
    encode_png,
    execute_steps,
    load_recipe,
    main,
    reserve_output,
    run_reload_process,
    sha256,
    validate_recipe,
    validate_steps,
    write_new,
)

RECIPES = Path(__file__).resolve().parents[1] / "tools" / "native_recipes"


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.recipe = load_recipe(RECIPES / "builtin-v10.json")

    def test_both_public_recipes_validate(self):
        self.assertEqual(self.recipe["versions"], ["1.0"])
        self.assertEqual(
            load_recipe(RECIPES / "builtin-v12-plus.json")["versions"], ["1.2", "1.33"]
        )

    def test_code_and_ram_write_commands_are_rejected(self):
        for op in (
            "write_memory",
            "set_memory",
            "unserialize",
            "exec",
            "python",
            "eval",
        ):
            with self.subTest(op=op), self.assertRaises(ValueError):
                validate_steps([{"op": op, "data": "arbitrary"}])

    def test_unknown_root_and_action_keys_rejected(self):
        for key in ("python", "source_path", "save_path"):
            recipe = copy.deepcopy(self.recipe)
            recipe[key] = "untrusted"
            with self.assertRaises(ValueError):
                validate_recipe(recipe)
        with self.assertRaises(ValueError):
            validate_steps([{"op": "run", "frames": 1, "write": "extra"}])

    def test_unbounded_frames_and_bad_capture_names_rejected(self):
        for frames in (0, -1, 5001, True, 1.2):
            with self.assertRaises(ValueError):
                validate_steps([{"op": "run", "frames": frames}])
        with self.assertRaises(ValueError):
            validate_steps([{"op": "run", "frames": 5000}] * 7)
        for name in ("../source", "a/b", "a.png", "C:\\input", "", "Upper"):
            with self.assertRaises(ValueError):
                validate_steps([{"op": "capture", "name": name}])

    def test_reload_cannot_replay_mutation_inputs(self):
        recipe = copy.deepcopy(self.recipe)
        recipe["reload"].insert(
            -1, {"op": "tap", "button": 8, "hold": 3, "release": 120}
        )
        with self.assertRaisesRegex(ValueError, "bootstrap"):
            validate_recipe(recipe)

    def test_fixture_assertions_and_save_wait_are_required(self):
        for before in ("0d000500", "0d000600"):
            recipe = copy.deepcopy(self.recipe)
            recipe["record"] = [
                s for s in recipe["record"] if s.get("expected") != before
            ]
            with self.assertRaises(ValueError):
                validate_recipe(recipe)
        recipe = copy.deepcopy(self.recipe)
        for step in recipe["record"]:
            if step.get("release") == 1200:
                step["release"] = 1
        with self.assertRaisesRegex(ValueError, "1200"):
            validate_recipe(recipe)

    def test_buttonsteps_only_accepts_button_frames_data(self):
        validate_steps(
            [
                {
                    "op": "buttonsteps",
                    "steps": [
                        {"buttons": [8], "frames": 2},
                        {"buttons": [], "frames": 20},
                    ],
                }
            ]
        )
        for buttons in ([16], [True], [8, 8], [[8]]):
            with self.assertRaises(ValueError):
                validate_steps(
                    [
                        {
                            "op": "buttonsteps",
                            "steps": [{"buttons": buttons, "frames": 2}],
                        }
                    ]
                )

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recipe.json"
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_recipe(path)


class PrivateOutputTests(unittest.TestCase):
    def test_fresh_directory_created_and_existing_directory_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input"
            source.write_bytes(b"original")
            output = reserve_output(Path(folder) / "new", [source])
            self.assertTrue(output.is_dir())
            with self.assertRaises(FileExistsError):
                reserve_output(output, [source])
            with self.assertRaises(FileExistsError):
                reserve_output(source, [source])
            self.assertEqual(source.read_bytes(), b"original")

    def test_exclusive_output_write_cannot_follow_existing_input_hardlink(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input"
            source.write_bytes(b"original")
            report = Path(folder) / "report.json"
            os.link(source, report)
            with self.assertRaises(FileExistsError):
                write_new(report, b"replacement")
            self.assertEqual(source.read_bytes(), b"original")

    def test_invalid_input_does_not_reserve_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "new"
            with self.assertRaises(FileNotFoundError):
                reserve_output(target, [Path(folder) / "missing"])
            self.assertFalse(target.exists())

    def test_record_failure_writes_false_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = {}
            for name in ("core", "source", "template", "ups", "save"):
                inputs[name] = root / name
                inputs[name].write_bytes(("synthetic-" + name).encode())
            argv = [
                arg for name, path in inputs.items() for arg in ("--" + name, str(path))
            ]
            argv += [
                "--output",
                str(root / "output"),
                "--expected-core-version",
                "synthetic",
            ]
            with (
                patch(
                    "tools.verify_builtin_native.perform_record",
                    side_effect=ValueError("synthetic fixture failure"),
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(argv), 1)
            result = json.loads(
                (root / "output" / "result.json").read_text(encoding="utf-8")
            )
            self.assertFalse(result["all_checks_passed"])
            self.assertIn("synthetic fixture failure", result["error"])
            for name, path in inputs.items():
                self.assertEqual(path.read_bytes(), ("synthetic-" + name).encode())


class BatteryAndExecutionTests(unittest.TestCase):
    def test_battery_lengths_and_rtc_tail(self):
        data = b"s" * BATTERY_SIZE
        self.assertEqual(battery_bytes(data), data)
        self.assertEqual(battery_bytes(data + b"r" * 16), data)
        for size in (0, BATTERY_SIZE - 1, BATTERY_SIZE + 1, BATTERY_SIZE + 17):
            with self.assertRaises(ValueError):
                battery_bytes(bytes(size))

    def test_version_pin_checked_before_any_battery_injection(self):
        core = Mock()
        core.load_game.return_value = {"library_version": "wrong"}
        with self.assertRaisesRegex(ValueError, "pin"):
            _load_core(core, "synthetic", bytes(BATTERY_SIZE), "expected", {})
        core.set_memory.assert_not_called()

    def test_only_save_ram_is_injected(self):
        core = Mock()
        core.load_game.return_value = {"library_version": "expected"}
        core.get_memory.return_value = bytes(BATTERY_SIZE)
        report = {}
        _load_core(core, "synthetic", bytes(BATTERY_SIZE), "expected", report)
        core.set_memory.assert_called_once_with(0, bytes(BATTERY_SIZE))
        core.write_memory.assert_not_called()
        core.unserialize.assert_not_called()
        self.assertFalse(report["rtc_injected"])

    def test_failed_assertion_never_marks_event_passed(self):
        core = SimpleNamespace(
            read_memory=lambda address, size: b"\x0d\x00\x05\x00",
            errors=[],
            frame_count=10,
        )
        events = []
        with self.assertRaisesRegex(ValueError, "Fixture"):
            execute_steps(
                core,
                [
                    {
                        "op": "assert_memory",
                        "address": "0203bb20",
                        "expected": "0d000600",
                    }
                ],
                Path("unused"),
                events,
            )
        self.assertFalse(events[0]["passed"])
        self.assertEqual(events[0]["actual"], "0d000500")

    def test_save_analysis_requires_complete_and_checksums_in_same_slot(self):
        module = SimpleNamespace(
            analyze_save=lambda rom, save: {
                "slots": [
                    {"complete": True, "base_checksums_valid": False},
                    {"complete": False, "base_checksums_valid": True},
                ]
            }
        )
        with (
            patch.dict("sys.modules", {"tools.inspect_mercury_save": module}),
            self.assertRaises(ValueError),
        ):
            _save_validation(b"synthetic", b"synthetic")
        module.analyze_save = lambda rom, save: {
            "slots": [{"complete": True, "base_checksums_valid": True}]
        }
        with patch.dict("sys.modules", {"tools.inspect_mercury_save": module}):
            self.assertTrue(
                _save_validation(b"synthetic", b"synthetic")["slots"][0]["complete"]
            )


class PngTests(unittest.TestCase):
    def test_png_contains_actual_frame_pixels_and_hash(self):
        frame = {
            "width": 2,
            "height": 1,
            "pitch": 6,
            "pixel_format": 2,
            "data": b"\x00\xf8\x00\x00XX",
        }
        png, pixel_hash = encode_png(frame)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(pixel_hash, sha256(b"\xff\x00\x00\x00\x00\x00"))
        offset, compressed = 8, b""
        while offset < len(png):
            length = struct.unpack_from(">I", png, offset)[0]
            kind = png[offset + 4 : offset + 8]
            data = png[offset + 8 : offset + 8 + length]
            crc = struct.unpack_from(">I", png, offset + 8 + length)[0]
            self.assertEqual(crc, zlib.crc32(kind + data) & 0xFFFFFFFF)
            if kind == b"IDAT":
                compressed += data
            offset += length + 12
        self.assertEqual(zlib.decompress(compressed), b"\x00\xff\x00\x00\x00\x00\x00")


class FreshProcessContractTests(unittest.TestCase):
    def test_reload_rejects_state_load_claim_even_if_child_claims_success(self):
        args = SimpleNamespace(core=Path("synthetic"), expected_core_version="expected")
        report = {"core_sha256": "c", "rom_sha256": "r", "battery_sha256": "b"}
        child = {
            **report,
            "all_checks_passed": True,
            "pid": os.getpid() + 100,
            "parent_pid": os.getpid(),
            "state_load_calls": 1,
            "direct_ram_edits": False,
            "battery_injected_bytes": BATTERY_SIZE,
            "rtc_injected": False,
            "core": {"library_version": "expected"},
        }
        process = Mock(pid=child["pid"], returncode=0)
        process.communicate.return_value = (json.dumps(child), "")
        with (
            patch("tools.verify_builtin_native.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(ValueError, "battery-only"),
        ):
            run_reload_process(args, Path("synthetic-output"), report)

    def test_child_nonzero_exit_cannot_become_success(self):
        args = SimpleNamespace(core=Path("synthetic"), expected_core_version="expected")
        process = Mock(pid=os.getpid() + 100, returncode=1)
        process.communicate.return_value = (
            '{"all_checks_passed": true}',
            "synthetic error",
        )
        with (
            patch("tools.verify_builtin_native.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(ValueError, "failed"),
        ):
            run_reload_process(args, Path("synthetic-output"), {})


if __name__ == "__main__":
    unittest.main()
