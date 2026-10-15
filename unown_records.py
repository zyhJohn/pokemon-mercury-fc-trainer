"""ROM-verified Unown capture and notebook records; pure reads/patch preparation.

These are Mercury's separate extended flags, not the national Pokédex.  Live
transactions, backups, ROM identity checks and restore belong to the caller.
"""

import json
from pathlib import Path

from game_fields import FieldPatch


LAYOUT = json.loads(Path(__file__).with_name("unown_records_layout.json").read_text(encoding="utf-8"))
CAPTURE_ADDRESS = LAYOUT["capture_address"]
CAPTURE_BYTES = LAYOUT["capture_bytes"]
RESEARCH_ADDRESS = 0x0203B1B0
RESEARCH_MASK = 0x3F
FINAL_ADDRESS = 0x0203B33E


def _release(sha):
    release = LAYOUT["releases"].get(sha)
    if release is None:
        raise ValueError("未知ROM：未知图腾记录布局未核验")
    return release


def flag_address(flag):
    """Only the bounded Mercury extended-flag range is accepted."""
    ext = LAYOUT["extended_flags"]
    if type(flag) is not int or not ext["first_flag"] <= flag <= ext["last_flag"]:
        raise ValueError("未知图腾记录旗标超出已核验范围")
    return ext["address"] + (flag - ext["first_flag"]) // 8


def unown_rom_features(rom_sha256):
    """Immutable ROM guards, including the complete audited NPC reward script.

    All eight features total 866 bytes. The caller compares them both before
    creating the snapshot and inside the same atomic callback as the RAM write.
    """
    return tuple((feature["address"], bytes.fromhex(feature["hex"]))
                 for feature in _release(rom_sha256)["runtime_features"])


def _read_exact(memory, address, size):
    raw = bytes(memory.read(address, size))
    if len(raw) != size:
        raise ValueError("未知图腾记录读取长度异常")
    return raw


def read_unown_records(memory, rom_sha256):
    """Read a stable snapshot, including named read-only final-page dependency.

    The caller must use the baseline layout identity for compatible ROMs and
    independently guard the actual connected ROM identity during transactions.
    """
    release = _release(rom_sha256)
    ranges = ((CAPTURE_ADDRESS, CAPTURE_BYTES), (RESEARCH_ADDRESS, 1), (FINAL_ADDRESS, 1))
    pointers = _read_exact(memory, 0x03005008, 8)
    for index in (0, 4):
        pointer = int.from_bytes(pointers[index:index + 4], "little")
        if pointer & 3 or not 0x02000000 <= pointer < 0x02040000:
            raise ValueError("存档块指针异常，拒绝读取未知图腾记录")
    records = {address: _read_exact(memory, address, size) for address, size in ranges}
    if pointers != _read_exact(memory, 0x03005008, 8) or any(
        raw != _read_exact(memory, address, len(raw)) for address, raw in records.items()
    ):
        raise ValueError("读取期间未知图腾记录或存档块指针变化，请重新读取")
    fields = []
    for index, letter in enumerate(LAYOUT["letters"]):
        flag = LAYOUT["first_capture_flag"] + index
        fields.append({"key": "captured_" + letter, "label": letter, "group": "captured",
                       "value": bool(records[CAPTURE_ADDRESS][flag_address(flag) - CAPTURE_ADDRESS] & (1 << (flag & 7))),
                       "editable": True, "flag": flag})
    for note in LAYOUT["notes"]:
        flag = note["flag"]
        fields.append({**note, "group": "research" if note["editable"] else "final",
                       "value": bool(records[flag_address(flag)][0] & (1 << (flag & 7))),
                       "description": "该位同时用于研究员对话阶段；修改不会自动补前置或发放奖励。"
                       if note["editable"] else
                       "该记录与任务034完成状态共用旗标；清除可能重开奖励，设置可能跳过未领奖励，因此只读。"})
    return {"rom_sha256": rom_sha256, "version": release["version"],
            "address": CAPTURE_ADDRESS, "raw": records[CAPTURE_ADDRESS],
            "records": records, "fields": fields,
            "task_accepted": bool(records[FINAL_ADDRESS][0] & 2),
            "task_complete": bool(records[FINAL_ADDRESS][0] & 4),
            "captured_count": sum(field["value"] for field in fields if field["group"] == "captured"),
            "guards": [(0x03005008, pointers), *records.items()]}


def prepare_unown_records_patches(snapshot, changes):
    """Prepare at most two patches, preserving every unrelated bit.

    No implicit dependency changes are made. Final-page completion, puzzles,
    rewards, key items and national Pokédex flags are never editable here.
    """
    _release(snapshot["rom_sha256"])
    if not isinstance(changes, dict):
        raise ValueError("未知图腾记录修改须为字段与布尔值映射")
    definitions = {"captured_" + letter: LAYOUT["first_capture_flag"] + index
                   for index, letter in enumerate(LAYOUT["letters"])}
    definitions.update({note["key"]: note["flag"] for note in LAYOUT["notes"] if note["editable"]})
    records = snapshot["records"]
    required = ((CAPTURE_ADDRESS, CAPTURE_BYTES), (RESEARCH_ADDRESS, 1), (FINAL_ADDRESS, 1))
    if set(records) != {address for address, _ in required} or any(
        not isinstance(records[address], bytes) or len(records[address]) != size for address, size in required
    ):
        raise ValueError("未知图腾记录快照范围或长度异常")
    updated = {address: bytearray(raw) for address, raw in records.items()}
    for key, value in changes.items():
        if key not in definitions or type(value) is not bool:
            raise ValueError("仅允许已核验的捕获字形与研究记录布尔字段")
        flag = definitions[key]
        address = flag_address(flag)
        base = CAPTURE_ADDRESS if CAPTURE_ADDRESS <= address < CAPTURE_ADDRESS + CAPTURE_BYTES else RESEARCH_ADDRESS
        mask = 1 << (flag & 7)
        updated[base][address - base] = (updated[base][address - base] & ~mask) | (mask if value else 0)
    return tuple(FieldPatch(address, before, bytes(updated[address]))
                 for address, before in records.items() if bytes(updated[address]) != before)


def completion_changes(snapshot):
    """Draft all research/capture requirements for normal NPC reward collection.

    Acceptance/completion flags and rewards remain untouched.  Completing a
    previously rewarded quest again is deliberately unsupported.
    """
    _release(snapshot["rom_sha256"])
    status = snapshot["records"][FINAL_ADDRESS][0]
    if status & 4:
        raise ValueError("任务034已完成，不提供重置或重复领奖")
    if not status & 2:
        raise ValueError("请先在游戏中接取阿露福遗迹之谜并获得笔记，再准备领奖条件")
    return {**{"captured_" + letter: True for letter in LAYOUT["letters"]},
            **{note["key"]: True for note in LAYOUT["notes"] if note["editable"]}}
