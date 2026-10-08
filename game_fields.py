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
    layout = repel_layout(memory, rom_sha256)
    address = layout[0]
    raw = memory.read(address, _REPEL["width"])
    if len(raw) != 2:
        raise ValueError("喷雾变量长度异常")
    if repel_layout(memory, rom_sha256) != layout or memory.read(address, 2) != raw:
        raise ValueError("读取时喷雾变量或保存结构指针已变化")
    return int.from_bytes(raw, "little"), raw


def repel_layout(memory, rom_sha256: str) -> tuple[int, bytes, bytes]:
    """Resolve Var 0x4020 from live SaveBlock1 and section-2 pointers."""
    if rom_sha256 not in _REPEL["verified_releases"]:
        raise ValueError("未知ROM：喷雾步数字段未核验")
    save1_raw = memory.read(_REPEL["saveblock1_pointer"], 4)
    save1 = int.from_bytes(save1_raw, "little")
    address = save1 + _REPEL["saveblock1_offset"]
    if save1 % 4 or not 0x02000000 <= save1 or address + 2 > 0x02040000:
        raise ValueError("喷雾 SaveBlock1 指针无效")
    source_raw = memory.read(_REPEL["save_section_source_pointer"], 4)
    source = int.from_bytes(source_raw, "little")
    if (source != save1 + _REPEL["source_offset"]
            or source + _REPEL["save_section_offset"] != address):
        raise ValueError("喷雾存档节源指针与 SaveBlock1 不一致")
    return address, save1_raw, source_raw


def prepare_repel_steps_patch(memory, rom_sha256: str, steps: int) -> FieldPatch:
    """Prepare a conservative 0..250 patch; never touches item quantities."""
    if type(steps) is not int or not _REPEL["edit_min"] <= steps <= _REPEL["edit_max"]:
        raise ValueError("喷雾剩余步数须为0～250")
    layout = repel_layout(memory, rom_sha256)
    current, before = read_repel_steps(memory, rom_sha256)
    if repel_layout(memory, rom_sha256) != layout:
        raise ValueError("准备喷雾补丁时保存结构指针已变化")
    if current > _REPEL["edit_max"]:
        raise ValueError("当前喷雾步数超出已核验范围，拒绝覆盖")
    return FieldPatch(layout[0], before, steps.to_bytes(2, "little"))
