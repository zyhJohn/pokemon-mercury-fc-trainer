import json
import os
import struct
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from clock_data import (
    FLASH_SIZE,
    SAVE_SIZE,
    calendar_text,
    decode_daily_event,
    decode_footer,
    digest,
    encode_footer,
    encode_daily_event,
    exclusive_save,
    local_epoch,
    parse_calendar,
    read_save,
    restore_calendar,
    save_info,
    write_calendar,
)
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


def make_save(value=None):
    value = value or datetime(2026, 10, 2, 22, 59, 4)
    data = bytearray(b"\xa7" * FLASH_SIZE)
    for base, counter in [(0, 40), (0xE000, 41)]:
        for n in range(14):
            # Rotate sections as the real flash-save format does.
            ident = (n + 3) % 14
            offset = base + n * 0x1000
            struct.pack_into(
                "<HHII", data, offset + 0xFF4, ident, 0, 0x08012025, counter
            )
            if ident == 0:
                data[offset + 14 : offset + 19] = struct.pack("<HBBB", 26, 4, 18, 255)
    return bytes(data) + encode_footer(value, local_epoch(value))


def put_time(memory, profile):
    for sig in profile["time"]["signatures"]:
        memory.put(sig["address"], bytes.fromhex(sig["hex"]))
    memory.put(0x03005EA0, struct.pack("<H7B", 2026, 0, 10, 2, 5, 22, 59, 4))
    memory.put(0x02024588 + 14, struct.pack("<HBBB", 26, 4, 18, 255))
    memory.put(
        profile["time"]["daily_event"]["address"],
        encode_daily_event(datetime(2026, 10, 4, 20, 3)),
    )


class PersistentTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="时间 测试 ")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / "中文 游戏.sav"
        self.rom = self.folder / "本地.gba"
        self.rom.write_bytes(b"verified test ROM")
        self.profile = {"rom_sha256": digest(self.rom.read_bytes())}
        self.path.write_bytes(make_save())
        self.backups = self.folder / "backups"
        self.now = local_epoch(datetime(2026, 10, 3, 1, 0, 0))
        self.clock = patch("clock_data.time.time", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def read(self):
        return read_save(self.path, self.rom, self.profile)

    def test_all_weekdays_and_leap_day_round_trip(self):
        monday = datetime(2026, 9, 28, 23, 59, 59)
        for day in range(7):
            value = monday + timedelta(days=day)
            raw = encode_footer(value, self.now)
            info = decode_footer(raw, self.now)
            self.assertEqual(info["current"], value)
            self.assertEqual(raw[3], (day + 1) % 7)
            self.assertIn(
                ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[day],
                calendar_text(value),
            )
        value = datetime(2024, 2, 29, 23, 59, 59)
        self.assertEqual(
            decode_footer(encode_footer(value, self.now), self.now + 1)["current"],
            datetime(2024, 3, 1),
        )

    def test_missing_footer_state_invalid_bcd_date_mode_rejected(self):
        original = make_save()
        for data in [original[:FLASH_SIZE], b"\0" * SAVE_SIZE, original + b"\0"]:
            with self.assertRaises(ValueError):
                save_info(data)
        footer = bytearray(original[-16:])
        for index, value in [
            (0, 0xFA),
            (1, 0),
            (2, 0x32),
            (3, 7),
            (4, 0x24),
            (7, 0),
            (8, 0xFF),
        ]:
            raw = footer.copy()
            raw[index] = value
            if index == 8:
                raw[8:] = b"\xff" * 8
            with self.assertRaises(ValueError):
                decode_footer(bytes(raw), self.now)
        for value in [
            "2026-02-29 00:00:00",
            "1999-12-31 23:59:59",
            "2100-01-01 00:00:00",
            "2026-10-01",
        ]:
            with self.assertRaises(ValueError):
                parse_calendar(value)

    def test_rotated_save_bank_and_counter_rollover(self):
        data = bytearray(make_save())
        for base, counter in [(0, 0xFFFFFFFF), (0xE000, 0)]:
            for n in range(14):
                struct.pack_into("<I", data, base + n * 0x1000 + 0xFFC, counter)
            zero = base + 11 * 0x1000
            struct.pack_into("<H", data, zero + 14, 100 if base == 0 else 200)
        info = save_info(bytes(data), self.now)
        self.assertEqual(info["counter"], 0)
        self.assertEqual(info["playtime"], (200, 4, 18, 255))

    def test_write_restart_progression_full_backup_and_condition_restore(self):
        snap = self.read()
        target = "2024-02-29 23:59:59"
        result = write_calendar(snap, target, self.rom, self.profile, self.backups)
        after = self.path.read_bytes()
        self.assertEqual(after[:FLASH_SIZE], snap["data"][:FLASH_SIZE])
        self.assertEqual(len(after), SAVE_SIZE)
        self.assertEqual(Path(result["save_backup"]).read_bytes(), snap["data"])
        self.assertEqual(
            decode_footer(after[-16:], self.now + 1)["current"], datetime(2024, 3, 1)
        )
        record = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "verified")
        restored = restore_calendar(
            self.read(), result["backup"], self.rom, self.profile, self.backups
        )
        self.assertTrue(restored["changed"])
        self.assertEqual(self.path.read_bytes(), snap["data"])

    def test_calibration_uses_commit_time_and_zero_offset(self):
        snap = self.read()
        result = write_calendar(
            snap, "old preview", self.rom, self.profile, self.backups, calibrate=True
        )
        info = self.read()
        self.assertEqual(info["offset"], 0)
        self.assertEqual(info["saved"], datetime.fromtimestamp(self.now))
        self.assertTrue(result["changed"])

    def test_weekday_mismatch_is_visible_and_normalized(self):
        data = bytearray(make_save())
        data[FLASH_SIZE + 3] = 1
        self.path.write_bytes(data)
        snap = self.read()
        self.assertTrue(snap["weekday_mismatch"])
        write_calendar(
            snap, "2026-10-02 22:59:04", self.rom, self.profile, self.backups
        )
        self.assertFalse(self.read()["weekday_mismatch"])

    def test_rom_mismatch_and_stale_whole_save_never_write(self):
        snap = self.read()
        self.rom.write_bytes(b"wrong ROM")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            write_calendar(
                snap, "2026-10-01 12:00:00", self.rom, self.profile, self.backups
            )
        self.rom.write_bytes(b"verified test ROM")
        data = bytearray(self.path.read_bytes())
        data[100] ^= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "已变化"):
            write_calendar(
                snap, "2026-10-01 12:00:00", self.rom, self.profile, self.backups
            )
        self.assertEqual(self.path.read_bytes(), bytes(data))
        self.assertFalse(list(self.backups.glob("*")))

    def test_restore_rejects_new_game_save_and_unconfirmed_or_tampered_record(self):
        result = write_calendar(
            self.read(), "2026-10-01 12:00:00", self.rom, self.profile, self.backups
        )
        record_path = Path(result["backup"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        data = bytearray(self.path.read_bytes())
        data[100] ^= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "存档已变化"):
            restore_calendar(
                self.read(), record_path, self.rom, self.profile, self.backups
            )
        data[100] ^= 1
        self.path.write_bytes(data)
        for change in [{"status": "prepared"}, {"before_sha256": "tampered"}]:
            record_path.write_text(json.dumps({**record, **change}), encoding="utf-8")
            with self.assertRaises(ValueError):
                restore_calendar(
                    self.read(), record_path, self.rom, self.profile, self.backups
                )

    def test_backup_failure_prevents_write(self):
        snap = self.read()
        with patch("clock_data.durable_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                write_calendar(
                    snap, "2026-10-01 12:00:00", self.rom, self.profile, self.backups
                )
        self.assertEqual(self.path.read_bytes(), snap["data"])
        self.assertEqual(next(self.backups.glob("*.sav")).read_bytes(), snap["data"])

    def test_partial_write_retains_full_backup_and_unconfirmed_status(self):
        snap = self.read()

        class PartialWriter:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, data):
                return self.stream.write(data[:5])

        @contextmanager
        def partial(path):
            with exclusive_save(path) as stream:
                yield PartialWriter(stream)

        with patch("clock_data.exclusive_save", partial):
            with self.assertRaisesRegex(OSError, "完整存档备份"):
                write_calendar(
                    snap, "2024-02-29 12:00:00", self.rom, self.profile, self.backups
                )
        self.assertEqual(next(self.backups.glob("*.sav")).read_bytes(), snap["data"])
        record = json.loads(
            next(self.backups.glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(record["status"], "failed-or-unconfirmed")
        self.assertEqual(self.path.read_bytes()[:FLASH_SIZE], snap["data"][:FLASH_SIZE])

    @unittest.skipUnless(os.name == "nt", "Windows release exclusive handle test")
    def test_open_game_file_is_refused_before_backup_or_write(self):
        snap = self.read()
        with exclusive_save(self.path):
            with self.assertRaisesRegex(OSError, "关闭 mGBA"):
                write_calendar(
                    snap, "2026-10-01 12:00:00", self.rom, self.profile, self.backups
                )
        self.assertEqual(self.path.read_bytes(), snap["data"])
        self.assertFalse(list(self.backups.glob("*")))


class LiveTimeTests(unittest.TestCase):
    def test_future_daily_record_repair_preview_write_restore_and_neighbours(self):
        for game_day in (2, 3):
            put_time(self.memory, self.profile)
            self.memory.put(0x03005EA0 + 4, bytes([game_day, (game_day + 3) % 7]))
            snap = self.trainer.snapshot_time()
            before, target = self.trainer.daily_repair_preview(snap)
            self.assertEqual(before, datetime(2026, 10, 4, 20, 3))
            self.assertEqual(target.day, game_day - 1)
            address = self.profile["time"]["daily_event"]["address"]
            self.memory.put(address - 1, b"\xab")
            self.memory.put(address + 4, b"\xcd")
            result = self.trainer.commit_daily_repair(snap)
            self.assertEqual(
                self.memory.read(address - 1, 6),
                b"\xab" + encode_daily_event(target) + b"\xcd",
            )
            self.assertEqual(
                self.trainer.snapshot_time()["daily_date"].day, game_day - 1
            )
            self.trainer.restore(result["backup"])
            self.assertEqual(self.memory.read(address, 4), snap["daily_raw"])
            self.assertEqual(self.memory.read(PARTY, 100), sample().raw)

    def test_daily_repair_requires_future_valid_record_and_stable_calendar(self):
        address = self.profile["time"]["daily_event"]["address"]
        for raw in (b"\0" * 4, b"\xff" * 4, encode_daily_event(datetime(2026, 10, 2))):
            self.memory.put(address, raw)
            with self.assertRaises(ValueError):
                self.trainer.commit_daily_repair(self.trainer.snapshot_time())
        for field in ("day", "record", "rtc_error"):
            put_time(self.memory, self.profile)
            snap = self.trainer.snapshot_time()
            if field == "day":
                self.memory.put(0x03005EA0 + 4, b"\x03\x06")
            elif field == "record":
                self.memory.put(address, encode_daily_event(datetime(2026, 10, 5)))
            else:
                self.memory.put(self.profile["time"]["rtc_error_address"], b"\x10\0")
            with self.assertRaises(OSError):
                self.trainer.commit_daily_repair(snap)
            self.assertEqual(self.memory.writes, 0)
            self.memory.put(self.profile["time"]["rtc_error_address"], b"\0\0")
        self.assertIsNone(decode_daily_event(b"\0" * 4))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))
        self.memory = Memory()
        for sig in self.profile["signatures"]:
            self.memory.put(sig["address"], bytes.fromhex(sig["hex"]))
        self.memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.memory.put(PARTY_COUNT, b"\1")
        self.memory.put(PARTY, sample().raw)
        put_time(self.memory, self.profile)
        self.trainer = Trainer(self.memory, self.profile, self.temp.name)

    def test_live_clock_weekday_and_waiting_frame_marker(self):
        snap = self.trainer.snapshot_time()
        self.assertEqual(snap["clock"], datetime(2026, 10, 2, 22, 59, 4))
        self.assertFalse(snap["weekday_mismatch"])
        self.assertEqual(snap["playtime"], (26, 4, 18, 255))
        self.assertEqual(self.memory.writes, 0)

    def test_playtime_write_and_restore_preserve_neighbours_and_clock(self):
        snap = self.trainer.snapshot_time()
        self.memory.put(snap["address"] + 13, b"\xab")
        self.memory.put(snap["address"] + 19, b"\xcd")
        clock = self.memory.read(0x03005EA0, 9)
        result = self.trainer.commit_playtime(snap, 999, 59, 59)
        self.assertEqual(
            self.memory.read(snap["address"] + 13, 7),
            b"\xab" + struct.pack("<HBBB", 999, 59, 59, 255) + b"\xcd",
        )
        self.trainer.restore(result["backup"])
        self.assertEqual(self.trainer.snapshot_time()["raw"], snap["raw"])
        self.assertEqual(self.memory.read(0x03005EA0, 9), clock)
        self.assertEqual(self.memory.read(PARTY, 100), sample().raw)

    def test_playtime_stale_pointer_seconds_and_battle_rejected(self):
        original_batch = self.memory.batch
        for field in ("pointer", "seconds", "battle"):
            self.memory.batch = original_batch
            put_time(self.memory, self.profile)
            snap = self.trainer.snapshot_time()
            battle = self.profile["battle_flag"]["address"]
            self.memory.put(battle, b"\0")
            if field == "pointer":
                self.memory.put(
                    self.profile["trainer"]["pointer_address"],
                    struct.pack("<I", snap["address"] + 4),
                )
            elif field == "seconds":

                def stale_before_callback(patches, verify=False):
                    self.memory.put(snap["address"] + 17, b"\x13")
                    original_batch(patches, verify=verify)

                self.memory.batch = stale_before_callback
            else:
                self.memory.put(battle, bytes([self.profile["battle_flag"]["mask"]]))
            with self.assertRaises((ValueError, OSError)):
                self.trainer.commit_playtime(snap, 20, 0, 0)
            self.assertEqual(self.memory.writes, 0)
            self.memory.put(
                self.profile["trainer"]["pointer_address"],
                struct.pack("<I", 0x02024588),
            )

    def test_natural_frame_and_second_progression_before_apply_is_preserved(self):
        snap = self.trainer.snapshot_time()
        self.memory.put(snap["address"] + 14, struct.pack("<HBBB", 26, 4, 19, 17))
        result = self.trainer.commit_playtime(snap, 10, 20, 30)
        self.assertEqual(self.trainer.snapshot_time()["playtime"], (10, 20, 30, 17))
        self.trainer.restore(result["backup"])
        self.assertEqual(self.trainer.snapshot_time()["playtime"], (26, 4, 19, 17))
        self.memory.capabilities = {"BATCH", "ROMCRC", "CRCBATCH", "BATCH8192"}
        previous = set(Path(self.temp.name).glob("*.json"))
        with self.assertRaisesRegex(ValueError, "0.2.10"):
            self.trainer.commit_playtime(snap, 1, 0, 0)
        self.assertEqual(previous, set(Path(self.temp.name).glob("*.json")))

    def test_time_signature_and_invalid_values_rejected(self):
        snap = self.trainer.snapshot_time()
        for values in [(-1, 0, 0), (1000, 0, 0), (0, 60, 0), (0, 0, 60)]:
            with self.assertRaises(ValueError):
                self.trainer.commit_playtime(snap, *values)
        signature = self.profile["time"]["signatures"][0]
        self.memory.put(signature["address"], b"\0")
        with self.assertRaisesRegex(ValueError, "ROM 函数"):
            self.trainer.snapshot_time()
        with self.assertRaises(OSError):
            self.trainer.commit_playtime(snap, 1, 0, 0)
        self.assertEqual(self.memory.writes, 0)
