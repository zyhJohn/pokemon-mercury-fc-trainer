"""Mercury calendar decoding and mGBA 0.10.5 persistent RTC footer editing.

The game clock cache is read-only: writing it is overwritten by the next RTC
sample. The footer lives outside the game's 128 KiB flash and its checksums.
"""

import ctypes
import hashlib
import json
import os
import struct
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

FLASH_SIZE = 0x20000
SAVE_SIZE = FLASH_SIZE + 16
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def calendar_text(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") + " · " + WEEKDAYS[value.weekday()]


def parse_calendar(value):
    try:
        result = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "日期时间格式应为 YYYY-MM-DD HH:MM:SS，且日期必须有效"
        ) from exc
    if not 2000 <= result.year <= 2099:
        raise ValueError("RTC 年份范围为 2000～2099")
    return result


def local_epoch(value):
    # Match mGBA's mktime(..., tm_isdst=-1), but reject a nonexistent local time.
    result = int(time.mktime(value.timetuple()))
    if datetime.fromtimestamp(result) != value:
        raise ValueError("该本地时间因夏令时切换不存在，请选择其他时间")
    return result


def bcd(byte):
    if byte >> 4 > 9 or byte & 15 > 9:
        raise ValueError("RTC 日期包含无效 BCD 数据")
    return (byte >> 4) * 10 + (byte & 15)


def encode_footer(value, epoch, control=0x40):
    parse_calendar(value.strftime("%Y-%m-%d %H:%M:%S"))
    if value.microsecond or value.tzinfo is not None:
        raise ValueError("请输入精确到秒的本地时间")
    if control not in (0x40, 0x48):
        raise ValueError("仅支持 mGBA 的 24 小时 RTC 模式")
    values = (
        value.year - 2000,
        value.month,
        value.day,
        (value.weekday() + 1) % 7,
        value.hour,
        value.minute,
        value.second,
    )
    return (
        bytes((v // 10 * 16 + v % 10) for v in values)
        + bytes([control])
        + struct.pack("<q", epoch)
    )


def decode_footer(raw, now=None):
    if len(raw) != 16 or raw[7] not in (0x40, 0x48):
        raise ValueError(
            "未识别的 RTC 尾部或非 24 小时模式；请先用 mGBA 0.10.5 保存游戏"
        )
    values = [bcd(v) for v in raw[:7]]
    if values[3] > 6:
        raise ValueError("RTC 星期编号无效")
    try:
        saved = datetime(2000 + values[0], values[1], values[2], *values[4:])
        epoch = struct.unpack_from("<q", raw, 8)[0]
        if (
            not local_epoch(datetime(2000, 1, 1))
            <= epoch
            <= local_epoch(datetime(2099, 12, 31, 23, 59, 59))
        ):
            raise ValueError("RTC 时间基准不在支持范围内")
        offset = epoch - local_epoch(saved)
        current = datetime.fromtimestamp(
            (int(time.time()) if now is None else now) - offset
        )
    except (OverflowError, OSError) as exc:
        raise ValueError("RTC 时间基准无效") from exc
    if not 2000 <= current.year <= 2099:
        raise ValueError("当前 RTC 推算时间不在 2000～2099 范围内")
    return {
        "saved": saved,
        "current": current,
        "offset": offset,
        "weekday_mismatch": values[3] != (saved.weekday() + 1) % 7,
    }


def decode_game_clock(raw):
    if len(raw) != 9:
        raise ValueError("游戏时钟长度无效")
    try:
        value = datetime(struct.unpack_from("<H", raw)[0], raw[3], raw[4], *raw[6:9])
    except ValueError as exc:
        raise ValueError("游戏时钟尚未初始化或日期无效，请进入游戏后重新读取") from exc
    if not 2000 <= value.year <= 2099 or raw[5] > 6:
        raise ValueError("游戏 RTC 年份或星期无效")
    return value, raw[5] != (value.weekday() + 1) % 7


def decode_playtime(raw):
    if len(raw) != 5:
        raise ValueError("累计游玩时长长度无效")
    hours, minutes, seconds, frames = struct.unpack("<HBBB", raw)
    if hours > 999 or minutes > 59 or seconds > 59:
        raise ValueError("累计游玩时长无效")
    # This ROM uses 0xFF as a legitimate waiting-for-RTC-second marker.
    return hours, minutes, seconds, frames


def decode_daily_event(raw):
    if len(raw) != 4:
        raise ValueError("每日事件日期长度无效")
    value = int.from_bytes(raw, "little")
    if value == 0:
        return None
    minute, hour = value & 63, (value >> 6) & 31
    day, month = (value >> 11) & 31, (value >> 16) & 15
    year, century = (value >> 20) & 127, value >> 27
    if year > 99:
        raise ValueError("每日事件年份编码无效")
    try:
        return datetime(century * 100 + year, month, day, hour, minute)
    except ValueError as exc:
        raise ValueError("每日事件日期未识别，保留原值") from exc


def encode_daily_event(value):
    parse_calendar(value.strftime("%Y-%m-%d %H:%M:%S"))
    packed = (
        value.minute
        | value.hour << 6
        | value.day << 11
        | value.month << 16
        | value.year % 100 << 20
        | value.year // 100 << 27
    )
    return packed.to_bytes(4, "little")


def save_info(data, now=None):
    if len(data) != SAVE_SIZE:
        raise ValueError("只支持带 16 字节 RTC 尾部的 128 KiB .sav；不支持即时存档")
    banks = []
    for base in (0, 0xE000):
        headers = [
            struct.unpack_from("<HHII", data, base + n * 0x1000 + 0xFF4)
            for n in range(14)
        ]
        if (
            {h[0] for h in headers} == set(range(14))
            and all(h[2] == 0x08012025 for h in headers)
            and len({h[3] for h in headers}) == 1
        ):
            section0 = (
                base + next(n for n, h in enumerate(headers) if h[0] == 0) * 0x1000
            )
            banks.append((headers[0][3], section0))
    if not banks:
        raise ValueError("没有完整的游戏存档槽；拒绝编辑未知或损坏的存档")
    # Modular save-counter ordering also handles rollover at 0xFFFFFFFF.
    chosen = banks[0]
    if len(banks) == 2 and 0 < (banks[1][0] - chosen[0]) & 0xFFFFFFFF < 0x80000000:
        chosen = banks[1]
    return {
        **decode_footer(data[FLASH_SIZE:], now),
        "counter": chosen[0],
        "playtime": decode_playtime(data[chosen[1] + 14 : chosen[1] + 19]),
    }


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_rom(rom_path, profile):
    if digest(Path(rom_path).read_bytes()) != profile["rom_sha256"]:
        raise ValueError("所选本地 ROM 的 SHA-256 与已验证版本不符")


def read_save(path, rom_path, profile):
    verify_rom(rom_path, profile)
    path = Path(path).resolve()
    if path.suffix.lower() != ".sav":
        raise ValueError("请选择游戏内保存的 .sav 文件")
    data = path.read_bytes()
    info = save_info(data)
    if path.read_bytes() != data:
        raise ValueError("读取期间存档已变化，请关闭游戏后重新读取")
    return {**info, "path": str(path), "data": data, "sha256": digest(data)}


@contextmanager
def exclusive_save(path):
    """Hold the same file handle throughout compare, backup, write and readback."""
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create.restype = wintypes.HANDLE
        handle = create(str(Path(path).resolve()), 0xC0000000, 0, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("存档无法独占打开；请关闭 mGBA 中的该游戏，并确认文件可写")
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except Exception:
            close = ctypes.WinDLL("kernel32").CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            close(handle)
            raise
        with os.fdopen(fd, "r+b") as stream:
            yield stream
    else:
        # Portable development fallback; supported release target is Windows.
        import fcntl

        with open(path, "r+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield stream


def durable_json(path, record):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def _write_footer(snapshot, footer, rom_path, profile, backup_dir, label):
    verify_rom(rom_path, profile)
    before = snapshot["data"]
    save_info(before)
    after = before[:FLASH_SIZE] + footer
    save_info(after)
    if before == after:
        return {"changed": False, "backup": None}
    folder = Path(backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-rtc-" + uuid4().hex[:8]
    path = folder / (name + ".json")
    backup = folder / (name + ".sav")
    record = {
        "schema": "mercury-rtc-1",
        "label": label,
        "status": "prepared",
        "rom_sha256": profile["rom_sha256"],
        "save_name": Path(snapshot["path"]).name,
        "before_sha256": digest(before),
        "after_sha256": digest(after),
        "body_sha256": digest(before[:FLASH_SIZE]),
        "full_backup": backup.name,
        "before_footer": before[FLASH_SIZE:].hex(),
        "after_footer": footer.hex(),
    }
    with exclusive_save(snapshot["path"]) as stream:
        if stream.read() != before:
            raise ValueError("存档自读取后已变化，请重新读取；未写入")
        with backup.open("xb") as output:
            output.write(before)
            output.flush()
            os.fsync(output.fileno())
        durable_json(path, record)
        try:
            stream.seek(FLASH_SIZE)
            if stream.write(footer) != 16:
                raise OSError("RTC 尾部未完整写入")
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != after:
                raise IOError("写后读回不一致，请核对完整存档备份")
            record["status"] = "verified"
        except Exception as exc:
            record["status"] = "failed-or-unconfirmed"
            record["error"] = str(exc)
            durable_json(path, record)
            raise IOError(f"{exc}\n完整存档备份：{backup}") from exc
        durable_json(path, record)
    return {"changed": True, "backup": str(path), "save_backup": str(backup)}


def write_calendar(snapshot, target, rom_path, profile, backup_dir, calibrate=False):
    epoch = int(time.time())
    value = datetime.fromtimestamp(epoch) if calibrate else parse_calendar(target)
    local_epoch(value)
    footer = encode_footer(value, epoch, snapshot["data"][FLASH_SIZE + 7])
    return _write_footer(
        snapshot,
        footer,
        rom_path,
        profile,
        backup_dir,
        "RTC 校准到电脑时间" if calibrate else "RTC 指定日期时间",
    )


def restore_calendar(snapshot, record_path, rom_path, profile, backup_dir):
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    if (
        record.get("schema") != "mercury-rtc-1"
        or record.get("status") != "verified"
        or record.get("rom_sha256") != profile["rom_sha256"]
    ):
        raise ValueError("RTC 备份格式、完成状态或 ROM 不匹配")
    if digest(snapshot["data"]) != record.get("after_sha256"):
        raise ValueError("存档已变化，不能条件恢复；请使用该次完整 .sav 备份另行核对")
    footer = bytes.fromhex(record["before_footer"])
    expected = snapshot["data"][:FLASH_SIZE] + footer
    if digest(expected) != record.get("before_sha256"):
        raise ValueError("备份校验失败")
    return _write_footer(
        snapshot, footer, rom_path, profile, backup_dir, "恢复 RTC 备份"
    )
