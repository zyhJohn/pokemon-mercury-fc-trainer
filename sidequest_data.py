"""Offline sidequest catalog and read-only progress decoding for verified ROMs."""

import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATUSES = frozenset(("unknown", "complete", "incomplete", "in_progress"))


def load_catalog(path=None):
    """Load the 96 wiki entries in their published order."""
    source = Path(path) if path is not None else ROOT / "sidequests.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    quests = document["quests"]
    if document["count"] != len(quests) or len({q["id"] for q in quests}) != len(quests):
        raise ValueError("支线任务目录数量或编号重复")
    if any(q["id"] != f"{index:03d}" for index, q in enumerate(quests, 1)):
        raise ValueError("支线任务目录顺序不符")
    return quests


def filter_quests(catalog, statuses, category, query=""):
    """Keep published order; unknown appears only in the full list."""
    if category not in ("all", "complete", "incomplete", "in_progress"):
        raise ValueError(f"未知支线任务分类：{category}")
    needle = query.strip().casefold()
    result = []
    for quest in catalog:
        status = statuses.get(quest["id"], "unknown")
        if category == "complete" and status != "complete":
            continue
        if category == "incomplete" and status not in ("incomplete", "in_progress"):
            continue
        if category == "in_progress" and status != "in_progress":
            continue
        haystack = " ".join((quest["id"], quest["title"], quest.get("summary", ""),
                             quest.get("objective", ""), " ".join(quest.get("tags", [])),
                             quest.get("details_text", ""))).casefold()
        if needle and needle not in haystack:
            continue
        result.append(quest)
    return result


def _flag_bytes(mem, quest_flags, ranges):
    flags = {entry[key] for entry in quest_flags.values()
             for key in ("accept_flag", "complete_flag")}
    flags.update(flag for entry in quest_flags.values()
                 for flag in entry.get("extra_accept_flags", []))
    values = {}
    for region in ranges:
        start, end = region["first_flag"], region["last_flag"]
        used = [flag for flag in flags if start <= flag <= end]
        if not used:
            continue
        if region["kind"] == "saveblock1":
            pointer = struct.unpack("<I", mem.read(region["pointer_address"], 4))[0]
            if not 0x02000000 <= pointer < 0x02040000:
                raise ValueError("SaveBlock1 指针越界")
            address = pointer + region["flags_offset"]
            offset = 0
        elif region["kind"] == "fixed_ram":
            address = region["address"]
            offset = start
        else:
            raise ValueError("未知标志存储区域")
        first_byte = (min(used) - offset) // 8
        last_byte = (max(used) - offset) // 8
        address += first_byte
        if not (0x02000000 <= address and address + last_byte - first_byte < 0x02040000):
            raise ValueError("标志读取范围越界")
        block = mem.read(address, last_byte - first_byte + 1)
        if len(block) != last_byte - first_byte + 1:
            raise ValueError("标志读取长度不足")
        for flag in used:
            values[flag] = bool(block[(flag - offset) // 8 - first_byte] & (1 << (flag & 7)))
    if values.keys() != flags:
        raise ValueError("任务标志不在已核对的读取范围")
    return values


def read_snapshot(mem, profile, layout=None, catalog=None):
    """Return id -> status from one live memory snapshot.

    The caller verifies the live ROM before this read. Unknown/failed memory
    cannot be presented as an uncompleted task. A completion flag takes
    precedence if an acceptance flag is absent or later cleared by the game.
    """
    quests = catalog if catalog is not None else load_catalog()
    unknown = {quest["id"]: "unknown" for quest in quests}
    if layout is None:
        layout = ROOT / "sidequests_layout.json"
    if not isinstance(layout, dict):
        layout = json.loads(Path(layout).read_text(encoding="utf-8"))
    sha = profile.get("rom_sha256")
    release = layout.get("releases", {}).get(sha)
    if not release:
        return unknown
    try:
        quest_flags = release["quest_flags"]
        if any(quest["id"] not in quest_flags for quest in quests):
            return unknown
        bits = _flag_bytes(mem, {quest["id"]: quest_flags[quest["id"]]
                                 for quest in quests}, release["flag_ranges"])
    except (KeyError, TypeError, ValueError, IndexError, OSError, struct.error):
        return unknown
    result = {}
    for quest in quests:
        entry = quest_flags[quest["id"]]
        if bits[entry["complete_flag"]]:
            result[quest["id"]] = "complete"
        elif bits[entry["accept_flag"]] or any(
                bits[flag] for flag in entry.get("extra_accept_flags", [])):
            result[quest["id"]] = "in_progress"
        else:
            result[quest["id"]] = "incomplete"
    return result
