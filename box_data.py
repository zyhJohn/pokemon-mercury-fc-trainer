"""Mercury FC compressed PC records, verified against ROM unpack code.

58 bytes per slot, 30 slots per box, 25 non-contiguous boxes. Do not turn this
into an 80-byte write path: edit only PID/IV/EV while retaining packed metadata.
"""

from dataclasses import dataclass
import struct
from pokemon_data import (
    experience_for_level,
    shiny_value,
    gender,
    six,
    change_shiny_pid,
    change_nature_pid,
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

    def edit(self, profile, *, ivs=None, evs=None, shiny=None, nature=None):
        """Change only directly verified fields; retain every other packed byte."""
        if not self.species:
            raise ValueError("空槽不能创建宝可梦")
        data = bytearray(self.raw)
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
        pid = self.pid
        if nature is not None:
            pid = change_nature_pid(pid, self.otid, nature, self.species)
        if shiny is not None:
            if not isinstance(shiny, bool):
                raise ValueError("闪光状态必须为是/否")
            pid = change_shiny_pid(pid, self.otid, shiny, self.species)
        struct.pack_into("<I", data, 0, pid)
        result = BoxPokemon(bytes(data))
        report = result.describe(profile)
        if report["errors"]:
            raise ValueError("；".join(report["errors"]))
        return result, report
