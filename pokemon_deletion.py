"""Removal plans for Mercury records; never reconstruct damaged Pokemon data."""

from pokemon_data import Pokemon, integer
from pokemon_integrity import inspect_record


def plan_deletion(raw, profile, kind, slot, *, count=None):
    """Return exact replacement bytes and diagnosis without modifying inputs."""
    if not isinstance(raw, bytes):
        raise TypeError("删除计划需要不可变的完整记录")
    if kind == "box":
        integer(slot, 0, 29, "盒子槽位")
        if len(raw) != 58:
            raise ValueError("PC删除必须保留完整58字节原像")
        diagnosis = inspect_record(raw, profile, "box")
        after = bytes(58)
    elif kind == "party":
        count = integer(count, 1, 6, "队伍数量")
        slot = integer(slot, 0, count - 1, "队伍槽位")
        if len(raw) != 600:
            raise ValueError("队伍删除必须保留完整六槽原像")
        records = [raw[i * 100 : (i + 1) * 100] for i in range(count)]
        diagnosis = inspect_record(records[slot], profile, "party")
        survivors = [record for index, record in enumerate(records) if index != slot]
        if not any(
            inspect_record(record, profile, "party")["editable"]
            and not Pokemon(record).egg
            for record in survivors
        ):
            raise ValueError("删除后必须至少保留一只健康且非蛋的队员，禁止空队")
        after = b"".join(survivors) + bytes(100) + raw[count * 100 :]
    else:
        raise ValueError("删除对象只能是队伍或PC")
    if diagnosis["empty"] or not diagnosis["damaged"]:
        raise ValueError("专用删除仅允许异常或坏蛋记录，不能删除健康宝可梦或空槽")
    return after, diagnosis


def deletion_patches(context, profile, party_address, count_address, box_addresses):
    """Rebuild a strict context before authorizing deletion or its reversal."""
    kind = context["kind"]
    before = bytes.fromhex(context["before"])
    after, _ = plan_deletion(
        before, profile, kind, context["slot"], count=context.get("count_before")
    )
    if after.hex() != context["after"]:
        raise ValueError("删除备份的目标与原记录重新计算结果不一致")
    if kind == "box":
        index = integer(context["box_index"], 0, 24, "盒子位置")
        return [(box_addresses[index] + context["slot"] * 58, before, after)]
    count = context["count_before"]
    result = [(count_address, bytes([count]), bytes([count - 1]))]
    # Include unchanged and inactive records as callback guards. This protects
    # survivor identity and ensures restoration cannot discard later changes.
    result += [
        (
            party_address + i * 100,
            before[i * 100 : (i + 1) * 100],
            after[i * 100 : (i + 1) * 100],
        )
        for i in range(6)
    ]
    return result
