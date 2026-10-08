"""Read-only daycare eligibility and exact one-byte pending-egg patch preparation.

Parent records are 80-byte plaintext BoxPokemon records in two 0x8C-byte
daycare slots. They are not the game's 58-byte compressed PC records.
The caller owns the live compare, backup, write, readback and recovery flow.
"""

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from name_codec import decode_name


_LAYOUT = json.loads(Path(__file__).with_name("daycare_layout.json").read_text(encoding="utf-8"))
_RELEASES = _LAYOUT["releases"]
_SAVE1_POINTER = 0x03005008
_SAVE1_OFFSET = _LAYOUT["daycare_offset"]
_PARENT_STRIDE = _LAYOUT["parent_stride"]
_PARENTS_SIZE = 2 * _PARENT_STRIDE
_DAYCARE_SIZE = _LAYOUT["step_counter_offset"] + 1
_FLAG_OFFSET = _LAYOUT["pending_saveblock1_offset"]
_FLAG_MASK = _LAYOUT["pending_mask"]


@dataclass(frozen=True)
class ParentRecord:
    raw_slot: bytes
    raw_mon: bytes
    species: int
    pid: int
    otid: int
    egg: bool
    steps: int
    nickname: str | None
    ot_name: str | None
    gender: str | None
    egg_groups: tuple[int, int] | None
    national_dex: int | None
    invalid_reason: str | None

    @property
    def is_egg(self) -> bool:
        return self.egg


@dataclass(frozen=True)
class DaycareSnapshot:
    rom_sha256: str
    saveblock1_address: int
    saveblock1_raw: bytes
    daycare_address: int
    daycare_raw: bytes
    parents: tuple[ParentRecord, ParentRecord]
    offspring_token: int
    step_counter: int
    pending_address: int
    pending_before: bytes
    pending: bool
    compatibility_score: int
    eligible: bool
    reason_code: str | None
    reason: str | None


@dataclass(frozen=True)
class PendingPatch:
    address: int
    before: bytes
    after: bytes


REASONS = {
    "invalid_parent_record": "培育屋父母记录异常，不能安全生成待领取蛋",
    "no_parents": "培育屋没有寄放宝可梦",
    "one_parent": "培育屋需寄放两只宝可梦",
    "parent_egg": "培育屋父母槽含已有蛋",
    "offspring_token": "培育屋旧后代标记非零，需先由游戏处理",
    "pending_egg": "培育屋已有待领取的蛋",
    "incompatible_parents": "寄放的两只宝可梦无法生蛋",
}


def _metadata(rom_sha256: str):
    release = _RELEASES.get(rom_sha256)
    if release is None:
        raise ValueError("未知ROM：培育屋布局未核验")
    return release["species"]


def _u32(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def parse_parent_record(raw_slot: bytes, rom_sha256: str) -> ParentRecord:
    """Parse actual daycare 80-byte mon prefix and adjacent step counter."""
    species_table = _metadata(rom_sha256)
    if len(raw_slot) != _PARENT_STRIDE:
        raise ValueError("培育屋父母槽长度错误")
    raw = raw_slot[:_LAYOUT["parent_record_size"]]
    species = struct.unpack_from("<H", raw, 32)[0]
    pid, otid = _u32(raw, 0), _u32(raw, 4)
    egg_bit = bool(_u32(raw, 72) & 0x40000000)
    egg_header = bool(raw[19] & 4)
    entry = species_table.get(str(species))
    invalid = None
    if species == 0:
        if any(raw):
            invalid = "空父母记录含非零残留"
    elif entry is None:
        invalid = "父母物种未在本版ROM核验"
    elif raw[19] & 1:
        invalid = "父母记录标记为坏蛋"
    elif egg_bit != egg_header:
        invalid = "父母蛋标志不一致"
    nickname = decode_name(raw[8:18]) if species else None
    ot_name = decode_name(raw[20:27]) if species else None
    if species and invalid is None and (nickname is None or ot_name is None):
        invalid = "父母姓名含未核验编码"
    gender = None
    groups = None
    national = None
    if entry is not None:
        g1, g2, national, ratio = entry
        groups = (g1, g2)
        if ratio == 255:
            gender = "无性别"
        elif ratio == 254 or (ratio and (pid & 255) < ratio):
            gender = "雌性"
        else:
            gender = "雄性"
    return ParentRecord(
        raw_slot=raw_slot, raw_mon=raw, species=species, pid=pid, otid=otid,
        egg=egg_bit or egg_header, steps=_u32(raw_slot, 0x88),
        nickname=nickname, ot_name=ot_name, gender=gender,
        egg_groups=groups, national_dex=national, invalid_reason=invalid,
    )


def compatibility_score(parents: tuple[ParentRecord, ParentRecord]) -> int:
    """Mirror the ROM's GetDaycareCompatibilityScore for valid parents."""
    a, b = parents
    if any(p.invalid_reason or not p.species or p.egg_groups is None for p in parents):
        return 0
    if a.egg_groups[0] == 15 or b.egg_groups[0] == 15:
        return 0
    ditto_a, ditto_b = a.egg_groups[0] == 13, b.egg_groups[0] == 13
    if ditto_a and ditto_b:
        return 0
    if ditto_a or ditto_b:
        return 20 if a.otid == b.otid else 50
    if a.gender == b.gender or "无性别" in (a.gender, b.gender):
        return 0
    if not any(x == y and x != 255 for x in a.egg_groups for y in b.egg_groups):
        return 0
    same_species = a.national_dex == b.national_dex
    same_ot = a.otid == b.otid
    if same_species:
        return 50 if same_ot else 70
    return 20 if same_ot else 50


def evaluate_eligibility(
    parents: tuple[ParentRecord, ParentRecord], token: int, pending: bool
) -> tuple[bool, str | None, str | None, int]:
    score = compatibility_score(parents)
    if any(parent.invalid_reason for parent in parents):
        code = "invalid_parent_record"
    elif not any(parent.species for parent in parents):
        code = "no_parents"
    elif not all(parent.species for parent in parents):
        code = "one_parent"
    elif any(parent.egg for parent in parents):
        code = "parent_egg"
    elif token:
        code = "offspring_token"
    elif pending:
        code = "pending_egg"
    elif score == 0:
        code = "incompatible_parents"
    else:
        code = None
    return code is None, code, REASONS.get(code), score


def read_daycare_snapshot(memory, rom_sha256: str) -> DaycareSnapshot:
    """Read coherent live data; the transaction must compare it again."""
    _metadata(rom_sha256)
    save1_raw = memory.read(_SAVE1_POINTER, 4)
    if len(save1_raw) != 4:
        raise ValueError("培育屋 SaveBlock1 指针读取不完整")
    save1 = _u32(save1_raw, 0)
    daycare_address = save1 + _SAVE1_OFFSET
    pending_address = save1 + _FLAG_OFFSET
    if (save1 % 4 or save1 < 0x02000000
            or daycare_address + _DAYCARE_SIZE > 0x02040000):
        raise ValueError("培育屋 SaveBlock1 指针无效")
    daycare_raw = memory.read(daycare_address, _DAYCARE_SIZE)
    pending_before = memory.read(pending_address, 1)
    if len(daycare_raw) != _DAYCARE_SIZE or len(pending_before) != 1:
        raise ValueError("培育屋读取不完整")
    if (memory.read(_SAVE1_POINTER, 4) != save1_raw
            or memory.read(daycare_address, _DAYCARE_SIZE) != daycare_raw
            or memory.read(pending_address, 1) != pending_before):
        raise ValueError("培育屋读取期间发生变化")
    parents = tuple(parse_parent_record(
        daycare_raw[i * _PARENT_STRIDE:(i + 1) * _PARENT_STRIDE], rom_sha256
    ) for i in range(2))
    token = struct.unpack_from("<H", daycare_raw, _LAYOUT["offspring_token_offset"])[0]
    step_counter = daycare_raw[_LAYOUT["step_counter_offset"]]
    pending = bool(pending_before[0] & _FLAG_MASK)
    eligible, code, reason, score = evaluate_eligibility(parents, token, pending)
    return DaycareSnapshot(
        rom_sha256=rom_sha256, saveblock1_address=save1,
        saveblock1_raw=save1_raw, daycare_address=daycare_address,
        daycare_raw=daycare_raw, parents=parents, offspring_token=token,
        step_counter=step_counter, pending_address=pending_address,
        pending_before=pending_before, pending=pending,
        compatibility_score=score, eligible=eligible,
        reason_code=code, reason=reason,
    )


def prepare_pending_patch(snapshot: DaycareSnapshot) -> PendingPatch:
    """Prepare Flag 0x266 only; all other bits of the byte are preserved."""
    if not isinstance(snapshot, DaycareSnapshot):
        raise ValueError("培育屋快照无效")
    if not snapshot.eligible or snapshot.pending:
        raise ValueError(snapshot.reason or "培育屋当前不能生成待领取蛋")
    before = snapshot.pending_before
    return PendingPatch(snapshot.pending_address, before, bytes((before[0] | _FLAG_MASK,)))
