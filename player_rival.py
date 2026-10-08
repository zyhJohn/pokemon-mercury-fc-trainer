"""Verified SaveBlock1 rival-name field, without player-gender editing."""

import json
from pathlib import Path

from name_codec import decode_name, encode_name


_LAYOUT = json.loads(
    Path(__file__).with_name("player_rival_layout.json").read_text(encoding="utf-8")
)
_RIVAL = _LAYOUT["rival_name"]
RIVAL_POINTER = int(_RIVAL["saveblock1_pointer"], 16)
RIVAL_OFFSET = int(_RIVAL["offset"], 16)
RIVAL_SIZE = _RIVAL["field_bytes"]


def rival_address(memory, rom_sha256):
    if rom_sha256 not in _LAYOUT["releases"]:
        raise ValueError("未知ROM：劲敌姓名字段未核验")
    pointer_raw = memory.read(RIVAL_POINTER, 4)
    pointer = int.from_bytes(pointer_raw, "little")
    address = pointer + RIVAL_OFFSET
    if pointer % 4 or not 0x02000000 <= pointer or address + RIVAL_SIZE > 0x02040000:
        raise ValueError("劲敌姓名存档结构指针无效")
    return address, pointer_raw


def decode_rival_name(raw):
    if len(raw) != RIVAL_SIZE or 0xFF not in raw:
        raise ValueError("劲敌姓名长度或终止符异常")
    end = raw.index(0xFF)
    if not 0 < end <= _RIVAL["maximum_content_bytes"]:
        raise ValueError("劲敌姓名内容长度异常")
    name = decode_name(raw[:end] + b"\xff")
    if name is None or not name.strip():
        raise ValueError("劲敌姓名含未知编码或为空")
    return name


def encode_rival_name(name):
    return encode_name(name, RIVAL_SIZE, maximum=_RIVAL["maximum_content_bytes"])
