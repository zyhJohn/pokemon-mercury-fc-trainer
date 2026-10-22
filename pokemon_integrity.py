"""Read-only integrity diagnostics for Mercury's plaintext party/packed PC data.

This checks verified structure, not encounter legality or editor value policies.
In particular +28 in a party record is NOT a checksum: Mercury reuses it for
form restoration and its Get/SetBoxMonData bypass the stock checksum paths.
No derived stats, image lookup, repair, or mutation takes place here.
"""

import struct


def inspect_record(raw: bytes, profile: dict, kind: str = "party", *, active=True):
    """Return a JSON-safe diagnostic, retaining IDs even when unrecognized.

    Only an entirely zero PC slot or inactive party slot is empty.  A zero
    species with residual bytes must never be treated as free storage.  The
    caller supplies ``active=False`` only for slots beyond the party count.
    """
    if kind not in ("party", "box"):
        raise ValueError("记录类型必须为 party 或 box")
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("宝可梦记录必须为字节数据")
    raw = bytes(raw)
    expected = 100 if kind == "party" else 58
    if len(raw) != expected:
        raise ValueError(f"{kind} 宝可梦记录必须为 {expected} 字节")

    def u16(offset):
        return struct.unpack_from("<H", raw, offset)[0]

    def u32(offset):
        return struct.unpack_from("<I", raw, offset)[0]

    party = kind == "party"
    species = u16(32 if party else 28)
    held = u16(34 if party else 30)
    iv_word = u32(72 if party else 54)
    packed_moves = int.from_bytes(raw[39:44], "little") if not party else 0
    moves = (
        [u16(44 + 2 * slot) for slot in range(4)]
        if party
        else [(packed_moves >> (10 * slot)) & 1023 for slot in range(4)]
    )
    flags = raw[19]
    egg_header = bool(flags & 4)
    egg_iv = bool(iv_word & (1 << 30))
    bad_egg = bool(flags & 1)
    all_zero = not any(raw)
    empty = all_zero and (not party or not active)
    observed = {
        "species": species,
        "held": held,
        "moves": moves,
        "pid": u32(0),
        "otid": u32(4),
        "flags": flags,
        "bad_egg_flag": bad_egg,
        "has_species_flag": bool(flags & 2),
        "egg_header": egg_header,
        "egg_iv": egg_iv,
        "egg": egg_iv,
        "iv_word": iv_word,
        "experience": u32(36 if party else 32),
        "evs": list(raw[56:62] if party else raw[44:50]),
    }
    if party:
        observed.update(
            level=raw[84],
            current_hp=u16(86),
            max_hp=u16(88),
            pp=list(raw[52:56]),
            form_restore_species=u16(28),
        )
    errors = []
    notes = ["水银不使用单体校验和；不重算或改写 +28 字段"]
    if not empty:
        if bad_egg:
            errors.append("坏蛋标志已设置（记录 +19 bit0）")
        if not species:
            errors.append(
                "有效队伍槽为全零记录" if all_zero else "物种为 0，但记录仍含非零残留"
            )
        elif str(species) not in profile.get("species", {}):
            errors.append(f"未知物种编号 {species}（不在当前 ROM 已验证表中）")
        if egg_header != egg_iv:
            errors.append("蛋标志不一致（+19 bit2 与 IV bit30）")
        for slot, move in enumerate(moves, 1):
            if move and str(move) not in profile.get("moves", {}):
                errors.append(f"招式槽 {slot} 含未知招式编号 {move}")
        if held and str(held) not in profile.get("items", {}):
            errors.append(f"未知携带道具编号 {held}")
        if party and not 1 <= raw[84] <= 100:
            errors.append(f"队伍等级 {raw[84]} 超出已验证范围 1～100")
    damaged = bool(errors)
    status = "empty" if empty else "bad_egg" if bad_egg else "damaged" if damaged else "healthy"
    return {
        "kind": kind,
        "status": status,
        "empty": empty,
        "damaged": damaged,
        "bad_egg": bad_egg,
        "editable": not empty and not damaged,
        "errors": errors,
        "notes": notes,
        "observed": observed,
        "checksum": "not-used-in-mercury",
    }


def require_editable(raw: bytes, profile: dict, kind: str = "party"):
    """Return the diagnostic or refuse ordinary editing of a damaged/empty slot."""
    report = inspect_record(raw, profile, kind)
    if not report["editable"]:
        detail = "；".join(report["errors"]) or "空槽不能编辑"
        raise ValueError(f"宝可梦记录不可编辑：{detail}")
    return report
