"""Mercury FC's verified fixed, plaintext 100-byte party representation.

This is structural validation, not an official encounter/RNG legality engine.
See docs/verified-layout.md for ROM signatures and offline evidence.
"""

import struct
from dataclasses import dataclass

from name_codec import decode_name, encode_name

STAT_NAMES = ("HP", "攻击", "防御", "速度", "特攻", "特防")
MINIOR_CORES = tuple(range(1065, 1072))
MINIOR_SPECIES = (991, *MINIOR_CORES)
MINIOR_COLORS = ("红色", "蓝色", "橙色", "黄色", "靛色", "绿色", "紫色")


def integer(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须为整数")
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}必须为整数") from None
    if isinstance(value, float) and value != result:
        raise ValueError(f"{label}必须为整数")
    if not low <= result <= high:
        raise ValueError(f"{label}须在 {low}～{high} 之间")
    return result


def six(values, maximum, label):
    if len(values) != 6:
        raise ValueError(f"{label}必须包含六项")
    return tuple(
        integer(v, 0, maximum, f"{label} {STAT_NAMES[i]}") for i, v in enumerate(values)
    )


def shiny_value(pid, otid):
    return ((pid >> 16) ^ (pid & 65535) ^ (otid >> 16) ^ (otid & 65535)) & 65535


def unown_form(pid):
    return (
        ((pid & 0x3000000) >> 18)
        | ((pid & 0x30000) >> 12)
        | ((pid & 0x300) >> 6)
        | (pid & 3)
    ) % 28


def same_pid_form(candidate, original, species):
    return (species != 201 or unown_form(candidate) == unown_form(original)) and (
        species not in MINIOR_SPECIES or candidate % 7 == original % 7
    )


def change_minior_color_pid(pid, otid, color):
    color = integer(color, 0, 6, "小陨星核心颜色")
    if pid % 7 == color:
        return pid
    shiny = shiny_value(pid, otid) < 8
    if shiny:
        tx = (otid >> 16) ^ (otid & 65535)
        values = (
            ((tx ^ low ^ value) << 16) | low
            for low in range(pid & 255, 65536, 256)
            for value in range(8)
        )
    else:
        values = ((high << 16) | (pid & 65535) for high in range(65536))
    matches = [
        value
        for value in values
        if value % 7 == color
        and value % 25 == pid % 25
        and (shiny_value(value, otid) < 8) == shiny
    ]
    if not matches:
        raise ValueError("无法同时保留当前闪光、性格、性别和特性修改核心颜色")
    return min(matches, key=lambda value: ((value ^ pid).bit_count(), value))


def change_shiny_pid(pid, otid, shiny, species):
    """Keep nature, low byte (gender/parity) and Unown letter where applicable.

    PID is not an encoding of the full Pokémon. Spinda pattern cannot be held
    constant when changing its PID; refuse instead of silently changing it.
    """
    if (shiny_value(pid, otid) < 8) == shiny:
        return pid
    if species == 308:  # Spinda's internal ID (national dex 327).
        raise ValueError("晃晃斑闪光修改会改变 PID 花纹，当前暂不支持")

    def matches(candidate):
        return (
            candidate % 25 == pid % 25
            and (candidate & 255) == (pid & 255)
            and same_pid_form(candidate, pid, species)
        )

    candidates = []
    if shiny:
        trainer_xor = (otid & 65535) ^ (otid >> 16)
        for low in range(pid & 255, 65536, 256):
            for value in range(8):
                high = trainer_xor ^ low ^ value
                candidate = (high << 16) | low
                if matches(candidate):
                    candidates.append(candidate)
    else:
        for high in range(65536):
            candidate = (high << 16) | (pid & 65535)
            if shiny_value(candidate, otid) >= 8 and matches(candidate):
                candidates.append(candidate)
    if not candidates:
        raise ValueError("未找到保持性格、性别和形态的闪光 PID，未修改")
    return min(candidates, key=lambda n: ((n ^ pid).bit_count(), n))


def gender(pid, ratio):
    if ratio == 255:
        return "无性别"
    if ratio == 254:
        return "雌性"
    if ratio == 0:
        return "雄性"
    return "雌性" if (pid & 255) < ratio else "雄性"


def change_unown_letter_pid(pid, otid, letter, ratio=255, preserve_parity=False):
    letter = integer(letter, 0, 27, "未知图腾字形")
    if unown_form(pid) == letter:
        return pid
    shiny = shiny_value(pid, otid) < 8
    old_gender = gender(pid, ratio)

    def matches(candidate):
        return (
            candidate % 25 == pid % 25
            and unown_form(candidate) == letter
            and gender(candidate, ratio) == old_gender
            and (not preserve_parity or candidate & 1 == pid & 1)
            and (shiny_value(candidate, otid) < 8) == shiny
        )

    candidates = []
    if shiny:
        tx = (otid >> 16) ^ (otid & 65535)
        for low in range(65536):
            if gender(low, ratio) != old_gender:
                continue
            for v in range(8):
                candidate = ((tx ^ low ^ v) << 16) | low
                if matches(candidate):
                    candidates.append(candidate)
    else:
        # Eight selected bits encode the letter; five free bits cover all
        # 25 nature residues without changing gender or selected form bits.
        for encoded in range(letter, 256, 28):
            base = pid & ~0x03030303
            for byte in range(4):
                base |= ((encoded >> (byte * 2)) & 3) << (byte * 8)
            for free in range(32):
                candidate = (base & ~0x007C0000) | (free << 18)
                if matches(candidate):
                    candidates.append(candidate)
    if not candidates:
        raise ValueError("此字形无法同时保留当前闪光、性格、性别和特性；未修改")
    return min(candidates, key=lambda n: ((n ^ pid).bit_count(), n))


def change_nature_pid(pid, otid, nature, species):
    nature = integer(nature, 0, 24, "性格")
    if pid % 25 == nature:
        return pid
    if species == 308:
        raise ValueError("晃晃斑修改性格会改变 PID 花纹，当前暂不支持")
    shiny = shiny_value(pid, otid) < 8
    if shiny:
        tx = (otid >> 16) ^ (otid & 65535)
        values = (
            (tx ^ low ^ v) << 16 | low
            for low in range(pid & 255, 65536, 256)
            for v in range(8)
        )
    else:
        values = (high << 16 | (pid & 65535) for high in range(65536))
    candidates = [
        n
        for n in values
        if n % 25 == nature
        and (shiny_value(n, otid) < 8) == shiny
        and same_pid_form(n, pid, species)
    ]
    if not candidates:
        raise ValueError("无法保留闪光、性别和形态修改性格")
    return min(candidates, key=lambda n: ((n ^ pid).bit_count(), n))


def regenerate_spinda_pid(pid, otid, seed, nature, shiny, ratio, parity=None):
    """Explicitly change spots while retaining chosen nature/shiny/gender/ability."""
    seed = integer(seed, 0, 0xFFFFFFFF, "花纹种子")
    nature = integer(nature, 0, 24, "性格")
    if not isinstance(shiny, bool):
        raise ValueError("闪光状态必须为是/否")
    old_gender = gender(pid, ratio)
    # A low y nibble of zero overflows the engine's first spot row. New
    # patterns avoid that case; old patterns are retained unless explicitly regenerated.
    low_bytes = [
        n
        for n in range(16, 256)
        if gender(n, ratio) == old_gender and (parity is None or n & 1 == parity)
    ]
    if not low_bytes:
        raise ValueError("无法保持性别与所选特性重新生成花纹")
    low = min(low_bytes, key=lambda n: ((n ^ (seed & 255)).bit_count(), n))
    for attempt in range(32):
        candidate = ((seed + attempt * 256) & 0xFFFFFF00) | low
        candidate = change_nature_pid(candidate, otid, nature, 0)
        candidate = change_shiny_pid(candidate, otid, shiny, 0)
        if candidate != pid:
            return candidate
    raise ValueError("没有生成不同的花纹，请再试一次")


def change_ability_pid(pid, otid, parity, species, ratio):
    if pid & 1 == parity:
        return pid
    if species == 308:
        raise ValueError("晃晃斑切换普通特性会改变 PID 花纹，当前暂不支持")
    if species == 201:
        raise ValueError("未知图腾字形与 PID 奇偶相关，不能保持字形切换普通特性")
    shiny = shiny_value(pid, otid) < 8
    old_gender = gender(pid, ratio)
    low_bytes = sorted(
        (n for n in range(parity, 256, 2) if gender(n, ratio) == old_gender),
        key=lambda n: ((n ^ (pid & 255)).bit_count(), n),
    )
    candidates = []
    trainer_xor = (otid >> 16) ^ (otid & 65535)
    for low_byte in low_bytes:
        if shiny:
            values = (
                (trainer_xor ^ low ^ v) << 16 | low
                for low in range(low_byte, 65536, 256)
                for v in range(8)
            )
        else:
            low = (pid & 0xFF00) | low_byte
            values = (high << 16 | low for high in range(65536))
        for candidate in values:
            if (
                candidate % 25 != pid % 25
                or (shiny_value(candidate, otid) < 8) != shiny
            ):
                continue
            if not same_pid_form(candidate, pid, species):
                continue
            candidates.append(candidate)
        if candidates:
            break
    if not candidates:
        raise ValueError("无法在保留性格、性别与闪光状态时切换特性")
    return min(candidates, key=lambda n: ((n ^ pid).bit_count(), n))


def calculate_stats(base, ivs, evs, level, nature, shedinja=False):
    stats = [
        (2 * base[i] + ivs[i] + evs[i] // 4) * level // 100
        + (level + 10 if i == 0 else 5)
        for i in range(6)
    ]
    if shedinja:
        stats[0] = 1
    up, down = nature // 5 + 1, nature % 5 + 1
    if up != down:
        stats[up] = stats[up] * 110 // 100
        stats[down] = stats[down] * 90 // 100
    return tuple(stats)


def experience_for_level(level, growth, tables):
    """Use exact ROM integers; textbook formulas differ at multiple boundaries."""
    n = integer(level, 1, 100, "等级")
    values = (tables or {}).get(str(growth))
    if not values or len(values) != 101:
        raise ValueError("缺少本地 ROM 已验证的经验表")
    return values[n]


@dataclass(frozen=True)
class Pokemon:
    raw: bytes

    def __post_init__(self):
        if len(self.raw) != 100:
            raise ValueError("队伍宝可梦必须为 100 字节")

    def u16(self, offset):
        return struct.unpack_from("<H", self.raw, offset)[0]

    def u32(self, offset):
        return struct.unpack_from("<I", self.raw, offset)[0]

    @property
    def pid(self):
        return self.u32(0)

    @property
    def otid(self):
        return self.u32(4)

    @property
    def friendship(self):
        return self.raw[41]

    @property
    def met_location(self):
        return self.raw[69]

    @property
    def met_level(self):
        return self.raw[70] & 127

    @property
    def ball(self):
        return self.raw[42]

    @property
    def ot_gender(self):
        return self.raw[71] >> 7

    @property
    def ot_name(self):
        return decode_name(self.raw[20:27])

    @property
    def species(self):
        return self.u16(32)

    @property
    def held(self):
        return self.u16(34)

    @property
    def experience(self):
        return self.u32(36)

    @property
    def moves(self):
        return struct.unpack_from("<4H", self.raw, 44)

    @property
    def pp(self):
        return tuple(self.raw[52:56])

    @property
    def evs(self):
        return tuple(self.raw[56:62])

    @property
    def ivs(self):
        return tuple((self.u32(72) >> (5 * i)) & 31 for i in range(6))

    @property
    def shiny(self):
        return shiny_value(self.pid, self.otid) < 8

    @property
    def egg(self):
        return bool(self.u32(72) & (1 << 30))

    @property
    def ability_flag(self):
        return self.u32(72) >> 31

    @property
    def level(self):
        return self.raw[84]

    @property
    def hp(self):
        return self.u16(86)

    @property
    def stats(self):
        return struct.unpack_from("<6H", self.raw, 88)

    def validate(self, base=None, ev_cap=252):
        errors, notes = [], []
        stats_match = None
        if not self.species:
            errors.append("物种编号为零")
        if self.raw[19] & 1:
            errors.append("游戏数据标记为坏蛋")
        if not 1 <= self.level <= 100:
            errors.append("等级超出 1～100")
        if self.hp > self.stats[0] or self.stats[0] == 0:
            errors.append("HP 与最大 HP 不一致")
        if sum(self.evs) > 510:
            errors.append("努力值总和超过 510")
        if max(self.evs) > ev_cap:
            errors.append(f"努力值单项超过 {ev_cap}")
        if bool(self.raw[19] & 4) != self.egg:
            errors.append("蛋标志在头部与数据区不一致")
        if base is not None:
            expected = calculate_stats(
                base, self.ivs, self.evs, self.level, self.pid % 25, self.species == 303
            )  # Shedinja internal ID.
            stats_match = expected == self.stats
            if expected != self.stats:
                notes.append(
                    "当前能力值与标准公式不同；修改 IV/EV 后将按已核对公式重算"
                )
        notes.append(
            "仅检查本改版结构与数值，招式来源仅部分核对；遭遇来源、PID/IV 随机生成关联及官方交换合法性未验证"
        )
        return {
            "errors": errors,
            "notes": notes,
            "structural_ok": not errors,
            "stats_match": stats_match,
            "official_legality": "not-checked",
        }

    def edit(
        self,
        *,
        ivs=None,
        evs=None,
        shiny=None,
        nature=None,
        base=None,
        species=None,
        level=None,
        growth=None,
        experience_tables=None,
        hp=None,
        held=None,
        moves=None,
        pp=None,
        move_data=None,
        ability_slot=None,
        abilities=None,
        gender_ratio=None,
        friendship=None,
        met_location=None,
        met_level=None,
        ball=None,
        ot_gender=None,
        ot_tid=None,
        ot_sid=None,
        unown_letter=None,
        ot_name=None,
        egg=None,
        egg_cycles=None,
        default_friendship=None,
        spinda_seed=None,
        minior_color=None,
    ):
        data = bytearray(self.raw)
        if ot_name is not None:
            data[20:27] = encode_name(ot_name, 7)
        species = (
            self.species if species is None else integer(species, 1, 65535, "物种")
        )
        if minior_color is not None:
            if self.species not in MINIOR_SPECIES or species not in MINIOR_SPECIES:
                raise ValueError("核心颜色编辑仅适用于小陨星")
            species = MINIOR_CORES[integer(minior_color, 0, 6, "小陨星核心颜色")]
        if species != self.species and self.u16(28):
            raise ValueError(
                "存在待还原的形态编号，请先让游戏结束形态还原并重新读取后再改物种"
            )
        level = self.level if level is None else integer(level, 1, 100, "等级")
        if egg is not None:
            if not isinstance(egg, bool):
                raise ValueError("蛋状态必须为是/否")
            if egg != self.egg:
                if egg:
                    if egg_cycles is None:
                        raise ValueError("缺少已核对的孵化周期")
                    level = 1
                    if friendship is None:
                        friendship = egg_cycles
                    if met_level is None:
                        met_level = 0
                elif friendship is None:
                    if default_friendship is None:
                        raise ValueError("缺少已核对的基础亲密度")
                    friendship = default_friendship
        for value, offset, maximum, label in [
            (friendship, 41, 255, "亲密度/孵化周期"),
            (met_location, 69, 255, "相遇地点编号"),
            (ball, 42, 255, "捕获球编号"),
        ]:
            if value is not None:
                data[offset] = integer(value, 0, maximum, label)
        if met_level is not None:
            data[70] = (data[70] & 128) | integer(met_level, 0, 127, "相遇等级")
        if ot_gender is not None:
            data[71] = (data[71] & 127) | (
                integer(ot_gender, 0, 1, "原训练师性别") << 7
            )
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
        if spinda_seed is not None:
            if (
                self.species != 308
                or species != 308
                or gender_ratio is None
                or not abilities
            ):
                raise ValueError("花纹重新生成仅适用于已核对的晃晃斑")
            parity = self.pid & 1 if abilities[1] and not self.ability_flag else None
            if ability_slot is not None and integer(ability_slot, 0, 2, "特性槽位") < 2:
                parity = int(ability_slot) if abilities[1] else None
            pid = regenerate_spinda_pid(
                self.pid,
                otid,
                spinda_seed,
                self.pid % 25 if nature is None else nature,
                self.shiny if shiny is None else shiny,
                gender_ratio,
                parity,
            )
            struct.pack_into("<II", data, 0, pid, otid)
        if otid != self.otid:
            target_shiny = self.shiny if shiny is None else shiny
            if not isinstance(target_shiny, bool):
                raise ValueError("闪光状态必须为是/否")
            pid = change_shiny_pid(
                struct.unpack_from("<I", data, 0)[0], otid, target_shiny, species
            )
            struct.pack_into("<II", data, 0, pid, otid)
        if (
            species != self.species
            or level != self.level
            or (egg is True and not self.egg)
        ):
            if growth is None:
                raise ValueError("缺少经验成长曲线")
            struct.pack_into("<H", data, 32, species)
            if (
                minior_color is None
                or level != self.level
                or (egg is True and not self.egg)
            ):
                struct.pack_into(
                    "<I",
                    data,
                    36,
                    experience_for_level(level, growth, experience_tables),
                )
            data[84] = level
        if held is not None:
            struct.pack_into("<H", data, 34, integer(held, 0, 749, "携带道具"))
        if moves is not None or pp is not None:
            if moves is None:
                moves = self.moves
            if pp is None:
                pp = self.pp
            if len(moves) != 4 or len(pp) != 4:
                raise ValueError("招式与 PP 必须各包含四项")
            moves = tuple(
                integer(v, 0, 65535, f"招式 {i + 1}") for i, v in enumerate(moves)
            )
            if len([m for m in moves if m]) != len(set(m for m in moves if m)):
                raise ValueError("同一只宝可梦不能重复填写相同招式")
            if not any(moves) and not self.egg:
                raise ValueError("非蛋宝可梦至少需要一个招式")
            pp_values = []
            for i, move in enumerate(moves):
                if move != self.moves[i]:
                    data[40] &= ~(3 << (2 * i))  # A new move does not inherit PP Ups.
                if move == 0:
                    data[40] &= ~(3 << (2 * i))
                    maximum = 0
                else:
                    metadata = (move_data or {}).get(str(move))
                    if not metadata:
                        raise ValueError(f"招式 {move} 不在本地 ROM 已验证表中")
                    bonus = (data[40] >> (2 * i)) & 3
                    maximum = metadata["pp"] * (5 + bonus) // 5
                pp_values.append(integer(pp[i], 0, maximum, f"招式 {i + 1} PP"))
            struct.pack_into("<4H4B", data, 44, *moves, *pp_values)
        if ivs is not None:
            ivs = six(ivs, 31, "个体值")
            word = self.u32(72) & 0xC0000000
            for i, v in enumerate(ivs):
                word |= v << (5 * i)
            struct.pack_into("<I", data, 72, word)
        if evs is not None:
            evs = six(evs, 252, "努力值")
            if sum(evs) > 510:
                raise ValueError("努力值总和不能超过 510；不会自动截断你的输入")
            data[56:62] = bytes(evs)
        if nature is not None:
            struct.pack_into(
                "<I",
                data,
                0,
                change_nature_pid(
                    struct.unpack_from("<I", data, 0)[0], otid, nature, species
                ),
            )
        if shiny is not None:
            if not isinstance(shiny, bool):
                raise ValueError("闪光状态必须为是/否")
            pid = change_shiny_pid(
                struct.unpack_from("<I", data, 0)[0], otid, shiny, species
            )
            struct.pack_into("<I", data, 0, pid)
        if ability_slot is not None:
            slot = integer(ability_slot, 0, 2, "特性槽位")
            if not abilities or not abilities[slot]:
                raise ValueError("该物种没有所选特性槽位")
            word = struct.unpack_from("<I", data, 72)[0]
            word = (word | 0x80000000) if slot == 2 else (word & 0x7FFFFFFF)
            struct.pack_into("<I", data, 72, word)
            if slot != 2:
                if gender_ratio is None:
                    raise ValueError("缺少性别比例数据")
                pid = struct.unpack_from("<I", data, 0)[0]
                pid = change_ability_pid(pid, otid, slot, species, gender_ratio)
                struct.pack_into("<I", data, 0, pid)
        if unown_letter is not None:
            if species != 201:
                raise ValueError("字形编辑仅适用于未知图腾")
            if gender_ratio is None or not abilities:
                raise ValueError("缺少已核对的性别/特性信息")
            pid = struct.unpack_from("<I", data, 0)[0]
            pid = change_unown_letter_pid(
                pid,
                otid,
                unown_letter,
                gender_ratio,
                bool(abilities[1]) and not bool(data[75] & 128),
            )
            struct.pack_into("<I", data, 0, pid)
        if egg is not None:
            data[19] = (data[19] | 4) if egg else (data[19] & ~4)
            word = struct.unpack_from("<I", data, 72)[0]
            word = (word | 0x40000000) if egg else (word & ~0x40000000)
            struct.pack_into("<I", data, 72, word)
        if minior_color is not None:
            pid = change_minior_color_pid(
                struct.unpack_from("<I", data, 0)[0], otid, minior_color
            )
            struct.pack_into("<I", data, 0, pid)
        updated = Pokemon(bytes(data))
        if (
            updated.ivs != self.ivs
            or updated.evs != self.evs
            or species != self.species
            or level != self.level
            or updated.pid % 25 != self.pid % 25
        ):
            if base is None:
                raise ValueError("缺少已验证的种族值，无法重算能力值")
            stats = calculate_stats(
                base, updated.ivs, updated.evs, level, updated.pid % 25, species == 303
            )
            # Preserve fainted state; otherwise preserve absolute HP damage.
            adjusted_hp = (
                0
                if self.hp == 0
                else max(1, min(stats[0], self.hp + stats[0] - self.stats[0]))
            )
            struct.pack_into("<H6H", data, 86, adjusted_hp, *stats)
        if hp is not None:
            struct.pack_into(
                "<H",
                data,
                86,
                integer(hp, 0, struct.unpack_from("<H", data, 88)[0], "当前 HP"),
            )
        updated = Pokemon(bytes(data))
        report = updated.validate(base)
        if report["errors"]:
            raise ValueError("；".join(report["errors"]))
        return updated, report
