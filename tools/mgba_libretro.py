"""Small synchronous ctypes libretro frontend for isolated research.

Only load trusted native libraries. The caller supplies the core and an existing
system directory; this frontend never writes saves, configuration, or reports.
All methods are single-threaded. RAM writes affect the running core only.

ABI: https://raw.githubusercontent.com/libretro/RetroArch/v1.19.1/libretro-common/include/libretro.h
Core: https://github.com/mgba-emu/mgba/tree/master/src/platform/libretro
"""

import ctypes as C
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

SAVE_RAM, RTC, SYSTEM_RAM, VIDEO_RAM = range(4)
B, Y, SELECT, START, UP, DOWN, LEFT, RIGHT, A, X, L, R = range(12)
MAX_BUFFER = 128 * 1024 * 1024
_ACTIVE_CORE_LOCK = Lock()


class LibretroError(RuntimeError):
    """Core failure or rejected operation."""


class SystemInfo(C.Structure):
    _fields_ = [
        ("library_name", C.c_char_p),
        ("library_version", C.c_char_p),
        ("valid_extensions", C.c_char_p),
        ("need_fullpath", C.c_bool),
        ("block_extract", C.c_bool),
    ]


class GameInfo(C.Structure):
    _fields_ = [
        ("path", C.c_char_p),
        ("data", C.c_void_p),
        ("size", C.c_size_t),
        ("meta", C.c_char_p),
    ]


class Geometry(C.Structure):
    _fields_ = [
        ("base_width", C.c_uint),
        ("base_height", C.c_uint),
        ("max_width", C.c_uint),
        ("max_height", C.c_uint),
        ("aspect_ratio", C.c_float),
    ]


class Timing(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class AVInfo(C.Structure):
    _fields_ = [("geometry", Geometry), ("timing", Timing)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class MemoryDescriptor(C.Structure):
    _fields_ = [
        ("flags", C.c_uint64),
        ("ptr", C.c_void_p),
        ("offset", C.c_size_t),
        ("start", C.c_size_t),
        ("select", C.c_size_t),
        ("disconnect", C.c_size_t),
        ("len", C.c_size_t),
        ("addrspace", C.c_char_p),
    ]


class MemoryMap(C.Structure):
    _fields_ = [
        ("descriptors", C.POINTER(MemoryDescriptor)),
        ("num_descriptors", C.c_uint),
    ]


@dataclass(frozen=True)
class MemoryRegion:
    flags: int
    ptr: int
    offset: int
    start: int
    select: int
    disconnect: int
    length: int
    addrspace: bytes


EnvironmentCB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VideoCB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AudioCB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AudioBatchCB = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
InputPollCB = C.CFUNCTYPE(None)
InputStateCB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


def frame_to_rgb(frame):
    """Convert a captured software frame into packed RGB bytes (no Pillow)."""
    width, height, pitch = (frame[k] for k in ("width", "height", "pitch"))
    fmt, data = frame["pixel_format"], frame["data"]
    bpp = 4 if fmt == 1 else 2
    if (
        fmt not in (0, 1, 2)
        or min(width, height) <= 0
        or width * height * 3 > MAX_BUFFER
    ):
        raise ValueError("Invalid frame geometry or pixel format")
    if pitch < width * bpp or len(data) != pitch * height:
        raise ValueError("Frame pitch/data length mismatch")
    result = bytearray(width * height * 3)
    out = 0
    for row in range(height):
        for col in range(width):
            p = row * pitch + col * bpp
            value = int.from_bytes(data[p : p + bpp], "little")
            if fmt == 1:
                r, g, b = (value >> 16) & 255, (value >> 8) & 255, value & 255
            else:
                r = (value >> (11 if fmt == 2 else 10)) & 31
                g = (value >> 5) & (63 if fmt == 2 else 31)
                b = value & 31
                r, b = (r << 3) | (r >> 2), (b << 3) | (b >> 2)
                g = ((g << 2) | (g >> 4)) if fmt == 2 else ((g << 3) | (g >> 2))
            result[out : out + 3] = bytes((r, g, b))
            out += 3
    return bytes(result)


class LibretroCore:
    """Run one trusted core; only one active instance is allowed per process."""

    def __init__(self, dll_path, system_dir):
        self.errors = []
        self.frame_count = 0
        self.video = None
        self.pixel_format = 0
        self.memory_regions = []
        self.variables = {}
        self._strings = []
        self._buttons = frozenset()
        self._closed = False
        self._loaded = False
        self._initialized = False
        self._owns_slot = False
        self._rom_buffer = None
        self._rom_path = None
        directory = Path(system_dir).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("system_dir must be an existing directory")
        self._directory = self._string(str(directory).encode("utf-8"))
        core_path = Path(dll_path).resolve(strict=True)
        self._owns_slot = _ACTIVE_CORE_LOCK.acquire(blocking=False)
        if not self._owns_slot:
            raise LibretroError("Only one active LibretroCore is allowed per process")
        try:
            self._initialize_core(core_path)
        except BaseException:
            self.close()
            raise

    def _initialize_core(self, core_path):
        self._dll = C.CDLL(str(core_path))
        self._bind()
        self._callbacks = (
            EnvironmentCB(self._guard(self._environment, False)),
            VideoCB(self._guard(self._video, None)),
            AudioCB(self._guard(lambda left, right: None, None)),
            AudioBatchCB(self._guard(lambda data, frames: frames, 0)),
            InputPollCB(self._guard(lambda: None, None)),
            InputStateCB(self._guard(self._input, 0)),
        )
        if self._dll.retro_api_version() != 1:
            raise LibretroError("Unsupported libretro ABI version")
        for name, cb in zip(
            (
                "environment",
                "video_refresh",
                "audio_sample",
                "audio_sample_batch",
                "input_poll",
                "input_state",
            ),
            self._callbacks,
        ):
            getattr(self._dll, "retro_set_" + name)(cb)
        self._dll.retro_init()
        self._initialized = True
        self._check_errors()

    @property
    def latest_video(self):
        return self.video

    def _bind(self):
        signatures = {
            "retro_api_version": ([], C.c_uint),
            "retro_init": ([], None),
            "retro_deinit": ([], None),
            "retro_get_system_info": ([C.POINTER(SystemInfo)], None),
            "retro_get_system_av_info": ([C.POINTER(AVInfo)], None),
            "retro_load_game": ([C.POINTER(GameInfo)], C.c_bool),
            "retro_unload_game": ([], None),
            "retro_run": ([], None),
            "retro_reset": ([], None),
            "retro_set_controller_port_device": ([C.c_uint, C.c_uint], None),
            "retro_get_memory_data": ([C.c_uint], C.c_void_p),
            "retro_get_memory_size": ([C.c_uint], C.c_size_t),
            "retro_serialize_size": ([], C.c_size_t),
            "retro_serialize": ([C.c_void_p, C.c_size_t], C.c_bool),
            "retro_unserialize": ([C.c_void_p, C.c_size_t], C.c_bool),
        }
        for name, kind in (
            ("environment", EnvironmentCB),
            ("video_refresh", VideoCB),
            ("audio_sample", AudioCB),
            ("audio_sample_batch", AudioBatchCB),
            ("input_poll", InputPollCB),
            ("input_state", InputStateCB),
        ):
            signatures["retro_set_" + name] = ([kind], None)
        for name, (args, result) in signatures.items():
            fn = getattr(self._dll, name)
            fn.argtypes, fn.restype = args, result

    def _guard(self, fn, fallback):
        def callback(*args):
            try:
                return fn(*args)
            except BaseException as exc:  # noqa: BLE001 - exceptions must never cross the C ABI.
                self.errors.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
                return fallback

        return callback

    def _string(self, value):
        buffer = C.create_string_buffer(value)
        self._strings.append(buffer)
        return C.cast(buffer, C.c_char_p)

    def _environment(self, command, data):
        # Accept both promoted and experimental forms, preserving PRIVATE flags.
        command &= ~0x10000
        if command == 51 or (command == 15 and not data):
            # GET_INPUT_BITMASKS ignores data; GET_VARIABLE permits support probes.
            return True
        if not data:
            return False
        if command in (9, 31):  # GET_SYSTEM_DIRECTORY / GET_SAVE_DIRECTORY
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._directory
            return True
        if command in (3, 17):  # CAN_DUPE / VARIABLE_UPDATE
            C.cast(data, C.POINTER(C.c_bool))[0] = command == 3
            return True
        if command == 10:  # SET_PIXEL_FORMAT
            fmt = C.cast(data, C.POINTER(C.c_int))[0]
            if fmt not in (0, 1, 2):
                return False
            self.pixel_format = fmt
            return True
        if command == 52:  # GET_CORE_OPTIONS_VERSION: request legacy variables.
            C.cast(data, C.POINTER(C.c_uint))[0] = 0
            return True
        if command == 16:  # SET_VARIABLES (NULL-key terminated)
            variables = C.cast(data, C.POINTER(Variable))
            defaults = {}
            for index in range(4096):
                item = variables[index]
                if item.key is None:
                    self.variables = defaults
                    return True
                if item.value is None or b";" not in item.value:
                    raise ValueError("Malformed legacy core option")
                default = item.value.split(b";", 1)[1].strip().split(b"|", 1)[0]
                defaults[bytes(item.key)] = self._string(default)
            raise ValueError("Unterminated legacy core options")
        if command == 15:  # GET_VARIABLE
            item = C.cast(data, C.POINTER(Variable)).contents
            item.value = self.variables.get(item.key)
            return True
        if command == 36:  # SET_MEMORY_MAPS: copy stack-backed metadata.
            mapping = C.cast(data, C.POINTER(MemoryMap)).contents
            if mapping.num_descriptors > 4096 or (
                mapping.num_descriptors and not mapping.descriptors
            ):
                raise ValueError("Invalid memory map descriptor count")
            self.memory_regions = [
                MemoryRegion(
                    int(d.flags),
                    int(d.ptr or 0),
                    int(d.offset),
                    int(d.start),
                    int(d.select),
                    int(d.disconnect),
                    int(d.len),
                    bytes(d.addrspace or b""),
                )
                for d in mapping.descriptors[: mapping.num_descriptors]
            ]
            return True
        if command == 32:  # SET_SYSTEM_AV_INFO
            info = C.cast(data, C.POINTER(AVInfo)).contents
            self.av_info = AVInfo.from_buffer_copy(info)
            return True
        if command == 37:  # SET_GEOMETRY
            self.geometry = Geometry.from_buffer_copy(
                C.cast(data, C.POINTER(Geometry)).contents
            )
            return True
        return False

    def _video(self, data, width, height, pitch):
        if not data:  # Duplicate previous frame.
            return
        if data == C.c_void_p(-1).value:
            raise ValueError("Hardware-rendered frames are unsupported")
        bpp = 4 if self.pixel_format == 1 else 2
        size = pitch * height
        if not width or not height or pitch < width * bpp or size > MAX_BUFFER:
            raise ValueError("Invalid or oversized software video frame")
        self.video = {
            "width": width,
            "height": height,
            "pitch": pitch,
            "pixel_format": self.pixel_format,
            "data": C.string_at(data, size),
        }

    def _input(self, port, device, index, button):
        if port != 0 or device != 1 or index != 0:
            return 0
        if button == 256:
            return C.c_int16(sum(1 << b for b in self._buttons)).value
        return int(button in self._buttons)

    def _check_errors(self):
        if self.errors:
            raise LibretroError("libretro callback error: " + self.errors[-1])

    def _require_loaded(self):
        if self._closed or not self._loaded:
            raise LibretroError("No running loaded game")

    def load_game(self, rom_path):
        if self._closed or self._loaded:
            raise LibretroError("Core closed or a game is already loaded")
        path = Path(rom_path).resolve(strict=True)
        info = SystemInfo()
        self._dll.retro_get_system_info(C.byref(info))
        self.system_info = {
            name: (getattr(info, name) or b"").decode("utf-8", "replace")
            for name in ("library_name", "library_version", "valid_extensions")
        }
        self.system_info.update(
            need_fullpath=bool(info.need_fullpath),
            block_extract=bool(info.block_extract),
        )
        self._rom_path = self._string(str(path).encode("utf-8"))
        game = GameInfo(self._rom_path, None, 0, None)
        if not info.need_fullpath:
            content = path.read_bytes()
            if not content or len(content) > MAX_BUFFER:
                raise ValueError("Empty or oversized ROM")
            self._rom_buffer = C.create_string_buffer(content)
            game.data, game.size = C.addressof(self._rom_buffer), len(content)
        if not self._dll.retro_load_game(C.byref(game)):
            raise LibretroError("Core rejected ROM")
        self._loaded = True
        self._dll.retro_set_controller_port_device(0, 1)
        self.av_info = AVInfo()
        self._dll.retro_get_system_av_info(C.byref(self.av_info))
        self._check_errors()
        return self.system_info.copy()

    def run(self, frames=1, buttons=()):
        self._require_loaded()
        if not isinstance(frames, int) or isinstance(frames, bool) or frames < 0:
            raise ValueError("frames must be a nonnegative integer")
        pressed = frozenset(buttons)
        if any(
            not isinstance(b, int) or isinstance(b, bool) or b < 0 or b > 15
            for b in pressed
        ):
            raise ValueError("buttons must contain libretro joypad IDs 0..15")
        self._buttons = pressed
        try:
            for _ in range(frames):
                self._dll.retro_run()
                self.frame_count += 1
                self._check_errors()
        finally:
            self._buttons = frozenset()
        return self.video

    def _memory(self, kind):
        self._require_loaded()
        if kind not in (SAVE_RAM, RTC, SYSTEM_RAM, VIDEO_RAM):
            raise ValueError("Unsupported libretro memory kind")
        size = self._dll.retro_get_memory_size(kind)
        ptr = self._dll.retro_get_memory_data(kind)
        if size > MAX_BUFFER or (size and not ptr):
            raise LibretroError("Invalid or oversized core memory")
        return ptr, size

    def get_memory(self, kind):
        ptr, size = self._memory(kind)
        return C.string_at(ptr, size) if size else b""

    def set_memory(self, kind, data):
        data = bytes(data)
        ptr, size = self._memory(kind)
        if len(data) != size:
            raise ValueError(f"Memory requires exactly {size} bytes")
        if size:
            C.memmove(ptr, data, size)

    def _resolve_region(self, address, size, writable):
        self._require_loaded()
        if (
            not isinstance(address, int)
            or not isinstance(size, int)
            or address < 0
            or size <= 0
        ):
            raise ValueError("address must be nonnegative and size positive")
        if size > MAX_BUFFER:
            raise ValueError("Memory access too large")
        for region in self.memory_regions:
            if not (region.start <= address < region.start + region.length):
                continue
            if address + size > region.start + region.length:
                raise ValueError("Memory access crosses region boundary")
            if region.disconnect or not region.ptr:
                raise ValueError("Disconnected or inaccessible memory map unsupported")
            if region.select and (
                (address & region.select) != (region.start & region.select)
                or ((address + size - 1) & region.select)
                != (region.start & region.select)
            ):
                raise ValueError("Address does not match selected memory region")
            if writable and region.flags & 1:
                raise ValueError("Read-only memory region")
            return region.ptr + region.offset + address - region.start
        raise ValueError("Address outside exact memory regions (mirrors unsupported)")

    def read_memory(self, address, size):
        return C.string_at(self._resolve_region(address, size, False), size)

    def write_memory(self, address, data):
        data = bytes(data)
        C.memmove(self._resolve_region(address, len(data), True), data, len(data))

    def serialize(self):
        self._require_loaded()
        size = self._dll.retro_serialize_size()
        if size <= 0 or size > MAX_BUFFER:
            raise LibretroError("Invalid serialization size")
        buffer = C.create_string_buffer(size)
        if not self._dll.retro_serialize(buffer, size):
            raise LibretroError("Core failed to serialize")
        self._check_errors()
        return buffer.raw

    def unserialize(self, data):
        self._require_loaded()
        data = bytes(data)
        size = self._dll.retro_serialize_size()
        if size <= 0 or size > MAX_BUFFER or len(data) != size:
            raise ValueError(f"State requires exactly {size} bytes")
        if not self._dll.retro_unserialize(C.create_string_buffer(data), len(data)):
            raise LibretroError("Core rejected serialized state")
        self._check_errors()

    def reset(self):
        self._require_loaded()
        self._dll.retro_reset()
        self._check_errors()

    def close(self):
        if self._closed:
            return
        try:
            if self._loaded:
                self._dll.retro_unload_game()
                self._loaded = False
        finally:
            try:
                if self._initialized:
                    self._dll.retro_deinit()
            finally:
                self._initialized = False
                self._closed = True
                self.memory_regions = []
                self._rom_buffer = None
                if self._owns_slot:
                    self._owns_slot = False
                    _ACTIVE_CORE_LOCK.release()

    def __enter__(self):
        if self._closed:
            raise LibretroError("Core already closed")
        return self

    def __exit__(self, *_):
        self.close()
