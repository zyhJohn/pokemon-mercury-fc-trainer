"""Create a complete Mercury FC 58-byte PC record for a supported ROM.

The layout and per-species header code were measured from both SHA-bound ROMs'
native 80-to-58 compressor. This constructs game data, not an official encounter
or event legality certificate. No save or emulator writes occur here.
"""

import json
import struct
from functools import lru_cache
from pathlib import Path

from box_data import BoxPokemon
from held_forms import held_form_family, held_form_species
from name_codec import encode_name
from pokemon_data import (
    MINIOR_CORES, Pokemon, calculate_stats, experience_for_level, integer,
    maximum_pp, six, toxtricity_species,
)


@lru_cache(maxsize=1)
def _layout():
    return json.loads(
        Path(__file__).with_name("pokemon_creation_layout.json").read_text(encoding="utf-8")
    )


def _source80(
    profile, *, species, level, pid, otid, nickname, ot_name, moves,
    pp_ups, ivs, evs, held, friendship, ball, met_location, met_level,
    ot_gender, egg, ability_slot, experience,
):
    if profile.get("rom_sha256") not in _layout()["rom_sha256"]:
        raise ValueError("创建仅支持已核对的水银 FC V1.0/V1.2 ROM 配置")
    species = integer(species, 1, 65535, "内部物种编号")
    metadata = profile["species"].get(str(species))
    if metadata is None:
        raise ValueError("物种编号不在当前ROM已核对表中")
    level = integer(level, 1, 100, "等级")
    pid = integer(pid, 0, 0xFFFFFFFF, "PID")
    otid = integer(otid, 0, 0xFFFFFFFF, "原训练师完整ID")
    if not isinstance(egg, bool):
        raise ValueError("蛋状态必须为是/否")
    if egg and level != 1:
        raise ValueError("新建蛋必须为1级")
    if len(moves) != 4 or len(pp_ups) != 4:
        raise ValueError("招式与PP提升必须各有四项")
    moves = tuple(integer(value, 0, 1023, "招式内部编号") for value in moves)
    if len(set(move for move in moves if move)) != sum(bool(move) for move in moves):
        raise ValueError("招式不能重复")
    if not egg and not any(moves):
        raise ValueError("非蛋个体至少需要一个招式")
    pp_ups = tuple(integer(value, 0, 3, "PP提升次数") for value in pp_ups)
    for move, ups in zip(moves, pp_ups):
        if not move and ups:
            raise ValueError("空招式不能设置PP提升")
        maximum_pp(move, ups, profile["moves"])
    ivs = six(ivs, 31, "个体值")
    evs = six(evs, 252, "努力值")
    if sum(evs) > 510:
        raise ValueError("努力值总和不能超过510")
    held = integer(held, 0, 749, "携带道具")
    item = profile["items"].get(str(held))
    if held and (not item or item["pocket"] in (2, 4)):
        raise ValueError("携带道具编号无效或属于重要道具/学习器")
    if ability_slot is None:
        ability_slot = 1 if metadata["abilities"][1] and pid & 1 else 0
    ability_slot = integer(ability_slot, 0, 2, "特性槽位")
    if not metadata["abilities"][ability_slot]:
        raise ValueError("物种没有所选特性槽位")
    if ability_slot < 2 and metadata["abilities"][1] and pid & 1 != ability_slot:
        raise ValueError("普通特性槽位与PID奇偶不一致")
    if species in (1141, 1193) and toxtricity_species(pid % 25) != species:
        raise ValueError("颤弦蝾螈形态与PID性格不一致")
    if species in MINIOR_CORES and MINIOR_CORES[pid % 7] != species:
        raise ValueError("小陨星核心颜色与PID不一致")
    if held_form_family(species) and held_form_species(
        species, held, metadata["abilities"][ability_slot], profile
    ) != species:
        raise ValueError("目标形态与携带道具或特性不一致")
    minimum = experience_for_level(level, metadata["growth"], profile["experience_tables"])
    maximum = (
        experience_for_level(level + 1, metadata["growth"], profile["experience_tables"]) - 1
        if level < 100 else minimum
    )
    experience = minimum if experience is None else integer(experience, minimum, maximum, "经验值")
    friendship = (
        metadata["egg_cycles"] if egg else metadata["friendship"]
    ) if friendship is None else integer(friendship, 0, 255, "亲密度/孵化周期")
    ball = integer(ball, 0, 255, "捕获球编号")
    ball_item = profile["items"].get(str(ball))
    if not ball_item or ball_item["pocket"] != 3:
        raise ValueError("新建个体的捕获球须属于本改版精灵球口袋")
    met_location = integer(met_location, 0, 255, "相遇地点编号")
    if met_location in profile.get("invalid_location_ids", []):
        raise ValueError("相遇地点编号对应本ROM无效名称指针")
    met_level = (0 if egg else level) if met_level is None else integer(met_level, 0, 127, "相遇等级")
    ot_gender = integer(ot_gender, 0, 1, "原训练师性别")
    raw = bytearray(80)
    struct.pack_into("<II", raw, 0, pid, otid)
    raw[8:18] = encode_name(nickname, 10, maximum=10)
    raw[18] = 2  # Actual native records use language 2.
    raw[19] = 2 | (4 if egg else 0)  # Present and egg flags.
    raw[20:27] = encode_name(ot_name, 7)
    struct.pack_into("<HHI", raw, 32, species, held, experience)
    raw[40] = sum(ups << (2 * index) for index, ups in enumerate(pp_ups))
    raw[41] = friendship
    raw[42] = ball
    struct.pack_into("<4H", raw, 44, *moves)
    raw[56:62] = bytes(evs)
    raw[69] = met_location
    raw[70] = met_level
    raw[71] = ot_gender << 7
    ivword = sum(value << (5 * index) for index, value in enumerate(ivs))
    ivword |= (1 << 30) if egg else 0
    ivword |= (1 << 31) if ability_slot == 2 else 0
    struct.pack_into("<I", raw, 72, ivword)
    return bytes(raw)


def create_box_pokemon(
    profile, *, species, level, pid, otid, nickname, ot_name, moves,
    pp_ups=(0, 0, 0, 0), ivs=(0, 0, 0, 0, 0, 0), evs=(0, 0, 0, 0, 0, 0),
    held=0, friendship=None, ball=4, met_location=0, met_level=None,
    ot_gender=0, egg=False, ability_slot=None, experience=None,
):
    """Build and validate a whole PC record; all IDs are ROM-internal IDs."""
    source = _source80(
        profile, species=species, level=level, pid=pid, otid=otid,
        nickname=nickname, ot_name=ot_name, moves=moves, pp_ups=pp_ups,
        ivs=ivs, evs=evs, held=held, friendship=friendship, ball=ball,
        met_location=met_location, met_level=met_level, ot_gender=ot_gender,
        egg=egg, ability_slot=ability_slot, experience=experience,
    )
    code = _layout()["form_codes"][str(species)][pid & 1]
    data = bytearray(58)
    data[:28] = source[:28]
    data[19] = (data[19] & 7) | (code << 3)
    data[28:39] = source[32:43]
    data[39:44] = sum(
        struct.unpack_from("<H", source, 44 + 2 * index)[0] << (10 * index)
        for index in range(4)
    ).to_bytes(5, "little")
    data[44:50] = source[56:62]
    data[50:58] = source[68:76]
    result = BoxPokemon(bytes(data))
    report = result.describe(profile)
    if report["errors"] or report["level"] != level:
        raise ValueError("创建记录验证失败：" + "；".join(report["errors"]))
    return result


def box_to_party_pokemon(profile, record):
    """Derive the game withdrawal's complete 100 bytes from a valid PC record."""
    if profile.get("rom_sha256") not in _layout()["rom_sha256"]:
        raise ValueError("队伍转换仅支持已核对的水银 FC V1.0/V1.2 ROM 配置")
    pc = record if isinstance(record, BoxPokemon) else BoxPokemon(record)
    if pc.species == 0:
        raise ValueError("全零或无物种PC空槽不能转换为队伍个体")
    description = pc.describe(profile)
    if description["errors"]:
        raise ValueError("PC记录无法取出：" + "；".join(description["errors"]))
    data = bytearray(100)
    data[:28] = pc.raw[:28]
    code = pc.raw[19] >> 3
    data[19] &= 7
    if code:
        struct.pack_into("<H", data, 30, 0xA500 | code)
    data[32:43] = pc.raw[28:39]
    struct.pack_into("<4H", data, 44, *pc.moves)
    for index, (move, ups) in enumerate(zip(pc.moves, pc.pp_ups)):
        data[52 + index] = 35 if move == 0 else maximum_pp(move, ups, profile["moves"])
    data[56:62] = pc.raw[44:50]
    data[68:76] = pc.raw[50:58]
    data[84] = description["level"]
    data[85] = 0xFF  # Empty mail slot on native withdrawal.
    metadata = profile["species"][str(pc.species)]
    stats = calculate_stats(
        metadata["base"], pc.ivs, pc.evs, description["level"],
        pc.pid % 25, pc.species == 303,
    )
    struct.pack_into("<H", data, 86, stats[0])
    struct.pack_into("<6H", data, 88, *stats)
    result = Pokemon(bytes(data))
    if result.validate(metadata["base"])["errors"]:
        raise ValueError("生成的队伍记录结构验证失败")
    return result
