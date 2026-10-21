"""Synthetic ABI and buffer checks; these tests do not load an emulator or ROM."""

import ctypes as C
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.mgba_libretro import (
    MAX_BUFFER,
    LibretroCore,
    LibretroError,
    MemoryDescriptor,
    MemoryMap,
    MemoryRegion,
    Variable,
    frame_to_rgb,
)


def synthetic_core():
    core = LibretroCore.__new__(LibretroCore)
    core.errors = []
    core.frame_count = 0
    core.video = None
    core.pixel_format = 0
    core.memory_regions = []
    core.variables = {}
    core._strings = []
    core._buttons = frozenset()
    core._closed = False
    core._loaded = True
    core._initialized = True
    core._owns_slot = False
    core._rom_buffer = None
    core._directory = core._string(b"synthetic-directory")
    return core


class LibretroSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.core = synthetic_core()
        self.buffer = C.create_string_buffer(b"abcdefgh", 8)
        self.core.memory_regions = [
            MemoryRegion(
                0, C.addressof(self.buffer), 0, 0x02000000, 0xFF000000, 0, 8, b""
            )
        ]

    def test_exact_region_reads_and_writes(self):
        self.assertEqual(self.core.read_memory(0x02000001, 3), b"bcd")
        self.core.write_memory(0x02000006, b"XY")
        self.assertEqual(self.buffer.raw, b"abcdefXY")

    def test_boundaries_and_mirrors_are_rejected(self):
        for address, size in [
            (0x02000007, 2),
            (0x02000008, 1),
            (0x02000100, 1),
            (0x01FFFFFF, 1),
            (0x02000000, 0),
            (0x02000000, MAX_BUFFER + 1),
        ]:
            with (
                self.subTest(address=address, size=size),
                self.assertRaises(ValueError),
            ):
                self.core.read_memory(address, size)
        self.assertEqual(self.buffer.raw, b"abcdefgh")

    def test_constant_region_is_readable_but_not_writable(self):
        region = self.core.memory_regions[0]
        self.core.memory_regions[0] = MemoryRegion(
            1, region.ptr, 0, region.start, region.select, 0, 8, b"ROM"
        )
        self.assertEqual(self.core.read_memory(region.start, 1), b"a")
        with self.assertRaisesRegex(ValueError, "Read-only"):
            self.core.write_memory(region.start, b"z")
        self.assertEqual(self.buffer.raw, b"abcdefgh")

    def test_offset_applied_once(self):
        self.core.memory_regions = [
            MemoryRegion(0, C.addressof(self.buffer), 4, 0x1000, 0, 0, 4, b"")
        ]
        self.assertEqual(self.core.read_memory(0x1000, 4), b"efgh")
        self.core.write_memory(0x1003, b"Z")
        self.assertEqual(self.buffer.raw, b"abcdefgZ")

    def test_first_matching_descriptor_controls_permission(self):
        region = self.core.memory_regions[0]
        self.core.memory_regions.insert(
            0, MemoryRegion(1, region.ptr, 0, region.start, region.select, 0, 8, b"")
        )
        with self.assertRaises(ValueError):
            self.core.write_memory(region.start, b"Z")

    def test_disconnect_and_null_pointer_rejected(self):
        for ptr, disconnected in [(0, 0), (C.addressof(self.buffer), 0x10)]:
            self.core.memory_regions = [
                MemoryRegion(0, ptr, 0, 0x1000, 0, disconnected, 8, b"")
            ]
            with self.assertRaises(ValueError):
                self.core.read_memory(0x1000, 1)

    def test_environment_copies_stack_descriptors(self):
        array = (MemoryDescriptor * 1)()
        array[0] = MemoryDescriptor(
            0, C.addressof(self.buffer), 1, 0x3000, 0, 0, 7, b"RAM"
        )
        mapping = MemoryMap(array, 1)
        self.assertTrue(self.core._environment(36 | 0x10000, C.byref(mapping)))
        array[0].start = 0x9999
        array[0].addrspace = b"CHANGED"
        self.assertEqual(self.core.read_memory(0x3000, 2), b"bc")
        self.assertEqual(self.core.memory_regions[0].addrspace, b"RAM")

    def test_experimental_forms_accept_but_private_forms_reject(self):
        array = (MemoryDescriptor * 1)()
        array[0] = MemoryDescriptor(
            0, C.addressof(self.buffer), 0, 0x3000, 0, 0, 8, b"RAM"
        )
        mapping = MemoryMap(array, 1)
        for command in (36, 36 | 0x10000):
            self.assertTrue(self.core._environment(command, C.byref(mapping)))
        for command in (51, 51 | 0x10000):
            self.assertTrue(self.core._environment(command, None))
        for command in (36 | 0x20000, 36 | 0x30000, 51 | 0x20000):
            self.assertFalse(self.core._environment(command, C.byref(mapping)))

    def test_legacy_options_default_and_pointer_lifetime(self):
        variables = (Variable * 3)(
            Variable(b"one", b"A label; first|second"),
            Variable(b"two", b"Another; enabled|disabled"),
            Variable(),
        )
        self.assertTrue(self.core._environment(16, C.byref(variables)))
        request = Variable(b"one", None)
        self.assertTrue(self.core._environment(15, C.byref(request)))
        self.assertEqual(request.value, b"first")
        self.assertEqual(len(self.core._strings), 3)
        request.key = b"missing"
        self.assertTrue(self.core._environment(15, C.byref(request)))
        self.assertIsNone(request.value)
        request.key = None
        self.assertTrue(self.core._environment(15, C.byref(request)))
        self.assertIsNone(request.value)
        self.assertTrue(self.core._environment(15, None))

    def test_environment_capabilities_and_directories(self):
        self.assertTrue(self.core._environment(51 | 0x10000, None))
        version = C.c_uint(99)
        self.assertTrue(self.core._environment(52, C.byref(version)))
        self.assertEqual(version.value, 0)
        for command in (9, 31):
            target = C.c_char_p()
            self.assertTrue(self.core._environment(command, C.byref(target)))
            self.assertEqual(target.value, b"synthetic-directory")
        for command, expected in ((3, True), (17, False)):
            flag = C.c_bool(not expected)
            self.assertTrue(self.core._environment(command, C.byref(flag)))
            self.assertEqual(flag.value, expected)
        for command in (27, 28, 65535):  # log/perf/unknown unsupported
            self.assertFalse(self.core._environment(command, C.byref(version)))

    def test_pixel_format_negotiation(self):
        for fmt in (0, 1, 2, 9):
            value = C.c_int(fmt)
            self.assertEqual(
                self.core._environment(10, C.byref(value)), fmt in (0, 1, 2)
            )
        self.assertEqual(self.core.pixel_format, 2)

    def test_video_copies_pitch_and_duplicate_preserves(self):
        self.core._video(C.addressof(self.buffer), 2, 2, 4)
        frame = self.core.video
        self.assertEqual(frame["data"], b"abcdefgh")
        self.buffer[0] = b"X"
        self.assertEqual(frame["data"], b"abcdefgh")
        self.core._video(None, 0, 0, 0)
        self.assertIs(self.core.video, frame)

    def test_callback_exceptions_recorded_without_escape(self):
        guarded = self.core._guard(self.core._video, None)
        self.assertIsNone(guarded(C.addressof(self.buffer), 99, 1, 2))
        self.assertIn("Invalid", self.core.errors[0])
        with self.assertRaises(LibretroError):
            self.core._check_errors()

    def test_input_uses_joypad_ids_and_signed_bitmask(self):
        self.core._buttons = frozenset((0, 8, 15))
        self.assertEqual(self.core._input(0, 1, 0, 8), 1)
        self.assertEqual(self.core._input(0, 1, 0, 1), 0)
        self.assertEqual(self.core._input(0, 1, 0, 256), -32511)
        self.assertEqual(self.core._input(1, 1, 0, 8), 0)
        self.assertEqual(self.core._input(0, 5, 0, 8), 0)

    def test_run_counts_frames_and_releases_buttons(self):
        seen = []
        self.core._dll = SimpleNamespace(
            retro_run=lambda: seen.append(self.core._buttons)
        )
        self.core.run(3, (8,))
        self.assertEqual(seen, [frozenset((8,))] * 3)
        self.assertEqual(self.core.frame_count, 3)
        self.assertFalse(self.core._buttons)
        for frames in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                self.core.run(frames)
        with self.assertRaises(ValueError):
            self.core.run(1, (256,))

    def test_memory_kind_requires_exact_size(self):
        self.core._dll = SimpleNamespace(
            retro_get_memory_size=lambda kind: 8,
            retro_get_memory_data=lambda kind: C.addressof(self.buffer),
        )
        self.assertEqual(self.core.get_memory(0), b"abcdefgh")
        with self.assertRaises(ValueError):
            self.core.set_memory(0, b"a")
        self.assertEqual(self.buffer.raw, b"abcdefgh")
        self.core.set_memory(0, b"12345678")
        self.assertEqual(self.buffer.raw, b"12345678")
        with self.assertRaises(ValueError):
            self.core.get_memory(4)

    def test_serialization_and_restore_exact_size(self):
        def serialize(ptr, size):
            C.memmove(ptr, b"state123", size)
            return True

        restored = []
        self.core._dll = SimpleNamespace(
            retro_serialize_size=lambda: 8,
            retro_serialize=serialize,
            retro_unserialize=lambda ptr, size: (
                restored.append(C.string_at(ptr, size)) or True
            ),
        )
        self.assertEqual(self.core.serialize(), b"state123")
        with self.assertRaises(ValueError):
            self.core.unserialize(b"short")
        self.assertEqual(restored, [])
        self.core.unserialize(b"state123")
        self.assertEqual(restored, [b"state123"])

    def test_close_is_idempotent_and_invalidates_access(self):
        unload, deinit = Mock(), Mock()
        self.core._dll = SimpleNamespace(retro_unload_game=unload, retro_deinit=deinit)
        self.core.close()
        self.core.close()
        unload.assert_called_once_with()
        deinit.assert_called_once_with()
        with self.assertRaises(LibretroError):
            self.core.read_memory(0x02000000, 1)


class CoreLifecycleTests(unittest.TestCase):
    """Use fake callable DLL exports, without loading any native code."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dll_path = Path(self.temp.name) / "synthetic.dll"
        self.dll_path.touch()
        self.dll = Mock()
        self.dll.retro_api_version.return_value = 1
        self.loader = patch("tools.mgba_libretro.C.CDLL", return_value=self.dll)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def make_core(self):
        core = LibretroCore(self.dll_path, self.temp.name)
        self.addCleanup(core.close)
        return core

    def test_active_instance_blocks_second_until_close(self):
        first = self.make_core()
        with self.assertRaisesRegex(LibretroError, "one active"):
            self.make_core()
        first.close()
        second = self.make_core()
        self.assertFalse(second._closed)
        self.assertEqual(self.dll.retro_init.call_count, 2)

    def test_init_callback_error_deinitializes_and_releases_slot(self):
        def fail_via_callback():
            video = self.dll.retro_set_video_refresh.call_args.args[0]
            video(C.c_void_p(-1), 1, 1, 2)

        self.dll.retro_init.side_effect = fail_via_callback
        with self.assertRaisesRegex(LibretroError, "callback error"):
            self.make_core()
        self.dll.retro_deinit.assert_called_once_with()
        self.dll.retro_unload_game.assert_not_called()
        self.dll.retro_init.side_effect = None
        self.make_core()

    def test_bad_abi_releases_slot_without_deinit(self):
        self.dll.retro_api_version.return_value = 2
        with self.assertRaisesRegex(LibretroError, "ABI"):
            self.make_core()
        self.dll.retro_deinit.assert_not_called()
        self.dll.retro_api_version.return_value = 1
        self.make_core()

    def test_load_library_exception_releases_slot(self):
        with (
            patch(
                "tools.mgba_libretro.C.CDLL", side_effect=OSError("synthetic failure")
            ),
            self.assertRaises(OSError),
        ):
            self.make_core()
        self.make_core()


class FrameConversionTests(unittest.TestCase):
    def test_all_formats_with_row_padding(self):
        for fmt, red in ((0, b"\x00\x7c"), (1, b"\x00\x00\xff\x00"), (2, b"\x00\xf8")):
            bpp = len(red)
            frame = {
                "width": 1,
                "height": 2,
                "pitch": bpp + 2,
                "pixel_format": fmt,
                "data": (red + b"XX") * 2,
            }
            self.assertEqual(frame_to_rgb(frame), b"\xff\x00\x00" * 2)

    def test_format_scaling_white_and_black(self):
        for fmt, white in (
            (0, b"\xff\x7f"),
            (1, b"\xff\xff\xff\x00"),
            (2, b"\xff\xff"),
        ):
            data = white + bytes(len(white))
            frame = {
                "width": 2,
                "height": 1,
                "pitch": len(data),
                "pixel_format": fmt,
                "data": data,
            }
            self.assertEqual(frame_to_rgb(frame), b"\xff\xff\xff\x00\x00\x00")

    def test_bad_frame_rejected(self):
        frame = {"width": 2, "height": 1, "pitch": 2, "pixel_format": 0, "data": b"xx"}
        with self.assertRaises(ValueError):
            frame_to_rgb(frame)


if __name__ == "__main__":
    unittest.main()
