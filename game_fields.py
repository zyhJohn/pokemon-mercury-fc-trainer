"""Verified, read-only game field layouts and pure patch preparation.

The caller is responsible for the live transaction: ROM checks, box locks,
old-value comparison, backup, write, readback, and conditional restore.
"""

from dataclasses import dataclass
import json
from pathlib import Path

from name_codec import decode_name, encode_name


_FIELDS = json.loads(
    Path(__file__).with_name("game_fields_layout.json").read_text(encoding="utf-8")
)
_LAYOUT = _FIELDS["box_names"]
_REPEL = _FIELDS["repel_steps"]
_FIRST_NAME = 0x020315F5
_NAME_COUNT = 25


@dataclass(frozen=True)
class FieldPatch:
    address: int
    before: bytes
    after: bytes


def box_name_address(rom_sha256: str, index: int) -> int:
    """Return the ROM's box-index mapping, for a supported exact ROM SHA."""
    if rom_sha256 not in _LAYOUT["verified_releases"]:
        raise ValueError("未知ROM：盒名字段未核验")
    if type(index) is not int or not 0 <= index < _NAME_COUNT:
        raise ValueError("盒子编号须为0～24")
    if index < 14:
        return _FIRST_NAME + 9 * (11 + index)
    return _FIRST_NAME + 9 * (10 - (index - 14))


def decode_box_name(record: bytes) -> str:
    if len(record) != _LAYOUT["record_bytes"] or b"\xff" not in record:
        raise ValueError("盒名记录长度或终止符异常")
    name = decode_name(record)
    if name is None or not name.strip():
        raise ValueError("盒名含未知编码或为空")
    return name


def encode_box_name(name: str) -> bytes:
    """Encode at most eight game text bytes, then FF and zero-filled tail."""
    encoded = encode_name(name, _LAYOUT["record_bytes"], maximum=8)
    payload = encoded.split(b"\xff", 1)[0]
    return payload + b"\xff" + bytes(8 - len(payload))


def read_box_name(memory, rom_sha256: str, index: int) -> tuple[str, bytes]:
    address = box_name_address(rom_sha256, index)
    raw = memory.read(address, _LAYOUT["record_bytes"])
    return decode_box_name(raw), raw


def prepare_box_name_patch(memory, rom_sha256: str, index: int, name: str) -> FieldPatch:
    """Prepare one exact 9-byte patch; this function never writes memory."""
    address = box_name_address(rom_sha256, index)
    before = memory.read(address, _LAYOUT["record_bytes"])
    decode_box_name(before)
    return FieldPatch(address, before, encode_box_name(name))


def read_repel_steps(memory, rom_sha256: str) -> tuple[int, bytes]:
    if rom_sha256 not in _REPEL["verified_releases"]:
        raise ValueError("未知ROM：喷雾步数字段未核验")
    raw = memory.read(_REPEL["address"], _REPEL["width"])
    if len(raw) != 2:
        raise ValueError("喷雾变量长度异常")
    return int.from_bytes(raw, "little"), raw


def prepare_repel_steps_patch(memory, rom_sha256: str, steps: int) -> FieldPatch:
    """Prepare a conservative 0..250 patch; never touches item quantities."""
    if type(steps) is not int or not _REPEL["edit_min"] <= steps <= _REPEL["edit_max"]:
        raise ValueError("喷雾剩余步数须为0～250")
    current, before = read_repel_steps(memory, rom_sha256)
    if current > _REPEL["edit_max"]:
        raise ValueError("当前喷雾步数超出已核验范围，拒绝覆盖")
    return FieldPatch(_REPEL["address"], before, steps.to_bytes(2, "little"))
