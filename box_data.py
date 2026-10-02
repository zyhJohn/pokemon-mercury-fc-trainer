"""Mercury FC compressed PC records, verified against ROM unpack code.

58 bytes per slot, 30 slots per box, 25 non-contiguous boxes. Do not turn this
into an 80-byte write path: retain all packed fields outside explicit edits.
"""

import struct
from dataclasses import dataclass

from name_codec import decode_name, encode_name
from pokemon_data import (
    MINIOR_CORES,
    MINIOR_SPECIES,
    TOXTRICITY_SPECIES,
    change_minior_color_pid,
    change_nature_pid,
    change_shiny_pid,
    change_unown_letter_pid,
    experience_for_level,
    gender,
    integer,
    regenerate_spinda_pid,
    shiny_value,
    six,
    toxtricity_species,
)


@dataclass(frozen=True)
class BoxPokemon:
    raw: bytes

    def __post_init__(self):
        if len(self.raw) != 58:
            raise ValueError("本改版盒子槽必须为 58 字节")

    def u16(self, o):
        return struct.unpack_from("<H", self.raw, o)[0]

    def u32(self, o):
        return struct.unpack_from("<I", self.raw, o)[0]

    @property
    def species(self):
        return self.u16(28)

    @property
    def pid(self):
        return self.u32(0)

    @property
    def otid(self):
        return self.u32(4)

    @property
    def ot_name(self):
        return decode_name(self.raw[20:27])

    @property
    def friendship(self):
        return self.raw[37]

    @property
    def ball(self):
        return self.raw[38]

    @property
    def met_location(self):
        return self.raw[51]

    @property
    def met_level(self):
        return self.raw[52] & 127

    @property
    def ot_gender(self):
        return self.raw[53] >> 7

    @property
    def held(self):
        return self.u16(30)

    @property
    def experience(self):
        return self.u32(32)

    @property
    def moves(self):
        packed = int.from_bytes(self.raw[39:44], "little")
        return tuple((packed >> (10 * i)) & 1023 for i in range(4))

    @property
    def ivs(self):
        return tuple((self.u32(54) >> (5 * i)) & 31 for i in range(6))

    @property
    def evs(self):
        return tuple(self.raw[44:50])

    @property
    def shiny(self):
        return shiny_value(self.pid, self.otid) < 8

    @property
    def egg(self):
        return bool(self.u32(54) & 0x40000000)

    @property
    def ability_flag(self):
        return self.u32(54) >> 31

    def describe(self, profile):
        result = {
            "species": self.species,
            "empty": not self.species,
            "errors": [],
            "level": None,
        }
        if not self.species:
            return result
        metadata = profile["species"].get(str(self.species))
        errors = result["errors"]
        if metadata:
            growth = metadata["growth"]
            tables = profile.get("experience_tables")
            result["level"] = max(
                (
                    n
                    for n in range(1, 101)
                    if experience_for_level(n, growth, tables) <= self.experience
                ),
                default=0,
            )
            if result["level"] == 0:
                errors.append("经验低于 1 级门槛")
            if self.experience > experience_for_level(100, growth, tables):
                errors.append("经验超过 100 级门槛")
            result["gender"] = gender(self.pid, metadata["gender_ratio"])
            abilities = metadata["abilities"]
            slot = (
                2
                if self.ability_flag and abilities[2]
                else (self.pid & 1 if abilities[1] else 0)
            )
            result["ability"] = abilities[slot]
            if not abilities[slot]:
                errors.append("当前特性槽位无可用特性")
        else:
            errors.append("物种编号尚未验证")
        if sum(self.evs) > 510 or max(self.evs) > 252:
            errors.append("EV 超出编辑器数值范围")
        if self.raw[19] & 1:
            errors.append("坏蛋标志已设置")
        if bool(self.raw[19] & 4) != self.egg:
            errors.append("蛋标志不一致")
        if any(m and str(m) not in profile["moves"] for m in self.moves):
            errors.append("包含尚未验证的招式编号")
        occupied = [m for m in self.moves if m]
        if len(set(occupied)) != len(occupied):
            errors.append("包含重复招式")
        if not occupied and not self.egg:
            errors.append("非蛋宝可梦至少需要一个招式")
        item = profile["items"].get(str(self.held))
        if self.held and (not item or item["pocket"] in (2, 4)):
            errors.append("携带道具编号无效或属于重要道具/学习器")
        result.update(
            pid=self.pid,
            shiny=self.shiny,
            ivs=self.ivs,
            evs=self.evs,
            moves=self.moves,
            held=self.held,
            egg=self.egg,
            experience=self.experience,
        )
        return result

    def edit(
        self,
        profile,
        *,
        ivs=None,
        evs=None,
        shiny=None,
        nature=None,
        unown_letter=None,
        friendship=None,
        ball=None,
        met_location=None,
        met_level=None,
        ot_tid=None,
        ot_sid=None,
        ot_gender=None,
        ot_name=None,
        spinda_seed=None,
        egg=None,
        minior_color=None,
    ):
        """Change only directly verified fields; retain every other packed byte."""
        if not self.species:
            raise ValueError("空槽不能创建宝可梦")
        if minior_color is not None and self.species not in MINIOR_SPECIES:
            raise ValueError("核心颜色编辑仅适用于小陨星")
        data = bytearray(self.raw)
        if egg is not None:
            if not isinstance(egg, bool):
                raise ValueError("蛋状态必须为是/否")
            metadata = profile["species"].get(str(self.species))
            if metadata is None:
                raise ValueError("缺少已核对物种信息")
            if egg != self.egg:
                if egg:
                    struct.pack_into(
                        "<I",
                        data,
                        32,
                        experience_for_level(
                            1, metadata["growth"], profile.get("experience_tables")
                        ),
                    )
                    if friendship is None:
                        friendship = metadata["egg_cycles"]
                    if met_level is None:
                        met_level = 0
                elif friendship is None:
                    friendship = metadata["friendship"]
        for value, offset, label in [
            (friendship, 37, "亲密度/孵化周期"),
            (ball, 38, "捕获球编号"),
            (met_location, 51, "相遇地点编号"),
        ]:
            if value is not None:
                data[offset] = integer(value, 0, 255, label)
        if met_level is not None:
            data[52] = (data[52] & 128) | integer(met_level, 0, 127, "相遇等级")
        if ot_gender is not None:
            data[53] = (data[53] & 127) | (
                integer(ot_gender, 0, 1, "原训练师性别") << 7
            )
        if ot_name is not None:
            data[20:27] = encode_name(ot_name, 7)
        tid = (
            self.otid & 65535
            if ot_tid is None
            else integer(ot_tid, 0, 65535, "原训练师 TID")
        )
        sid = (
            self.otid >> 16
            if ot_sid is None
            else integer(ot_sid, 0, 65535, "原训练师 SID")
        )
        otid = tid | sid << 16
        pid = self.pid
        if spinda_seed is not None:
            metadata = profile["species"].get(str(self.species))
            if self.species != 308 or metadata is None:
                raise ValueError("花纹重新生成仅适用于已核对的晃晃斑")
            pid = regenerate_spinda_pid(
                pid,
                otid,
                spinda_seed,
                pid % 25 if nature is None else nature,
                self.shiny if shiny is None else shiny,
                metadata["gender_ratio"],
                self.pid & 1
                if metadata["abilities"][1] and not self.ability_flag
                else None,
            )
        if otid != self.otid:
            target_shiny = self.shiny if shiny is None else shiny
            if not isinstance(target_shiny, bool):
                raise ValueError("闪光状态必须为是/否")
            pid = change_shiny_pid(pid, otid, target_shiny, self.species)
            struct.pack_into("<I", data, 4, otid)
        if ivs is not None:
            values = six(ivs, 31, "个体值")
            word = (self.u32(54) & 0xC0000000) | sum(
                v << (5 * i) for i, v in enumerate(values)
            )
            struct.pack_into("<I", data, 54, word)
        if evs is not None:
            values = six(evs, 252, "努力值")
            if sum(values) > 510:
                raise ValueError("努力值总和不能超过 510")
            data[44:50] = bytes(values)
        if nature is not None:
            pid = change_nature_pid(pid, otid, nature, self.species)
            if self.species in TOXTRICITY_SPECIES and pid % 25 != self.pid % 25:
                struct.pack_into("<H", data, 28, toxtricity_species(pid % 25))
        if shiny is not None:
            if not isinstance(shiny, bool):
                raise ValueError("闪光状态必须为是/否")
            pid = change_shiny_pid(pid, otid, shiny, self.species)
        if unown_letter is not None:
            if self.species != 201:
                raise ValueError("字形编辑仅适用于未知图腾")
            metadata = profile["species"].get(str(self.species))
            if metadata is None:
                raise ValueError("缺少已核对物种信息")
            pid = change_unown_letter_pid(
                pid,
                otid,
                unown_letter,
                metadata["gender_ratio"],
                bool(metadata["abilities"][1]) and not self.ability_flag,
            )
        if egg is not None:
            data[19] = (data[19] | 4) if egg else (data[19] & ~4)
            word = struct.unpack_from("<I", data, 54)[0]
            word = (word | 0x40000000) if egg else (word & ~0x40000000)
            struct.pack_into("<I", data, 54, word)
        if minior_color is not None:
            color = integer(minior_color, 0, 6, "小陨星核心颜色")
            pid = change_minior_color_pid(pid, otid, color)
            struct.pack_into("<H", data, 28, MINIOR_CORES[color])
        struct.pack_into("<I", data, 0, pid)
        result = BoxPokemon(bytes(data))
        report = result.describe(profile)
        if report["errors"]:
            raise ValueError("；".join(report["errors"]))
        return result, report
