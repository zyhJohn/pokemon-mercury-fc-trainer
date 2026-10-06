"""Verified ROM profile, immutable read snapshots, durable backups and transactions."""

import json
import hashlib
import os
import struct
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from box_data import BoxPokemon
from distribution_catalog import load_distributions, usable_rows
from game_fields import (
    box_name_address,
    decode_box_name,
    encode_box_name,
)
from clock_data import (
    decode_daily_event,
    decode_game_clock,
    decode_playtime,
    encode_daily_event,
)
from move_sources import describe_move_sources
from name_codec import decode_name, encode_name
from held_forms import held_form_family, held_form_species, resolve_held_form
from pokemon_data import (
    MINIOR_COLORS,
    MINIOR_CORES,
    MINIOR_SPECIES,
    TOXTRICITY_SPECIES,
    Pokemon,
    experience_for_level,
    integer,
    resolve_toxtricity_form,
    toxtricity_species,
)
from pokemon_creation import box_to_party_pokemon, create_box_pokemon

PARTY = 0x02024284
PARTY_COUNT = 0x02024029
SAVE_POINTER = 0x03005008

_NATIVE_GIFT_HASHES = {
    "mercury-home-165": "183bddac03ade1d14792a47b3800172346bc108a5d5a996498c415755a7ba7b2",
    "mercury-home-496": "1ead7463ec5ac2557c1b4bd4fda58984957f51d1023b3b0d8110ec0fffdda765",
    "mercury-home-1411": "c932a81176fb4810c3bc187aedfdf6fbd922878de0ee337e38eede6f33e3c27b",
    "mercury-home-1408": "2ebabb0836188e14e533ce788549a6ce389b28c7e0075b8342e2aa1ae90da56d",
    "gen3-rsefl-10-aniv-celebi-0bf5": "4481b441293d2b82763ca3cdb94fbec498f5328c13a638547c8f0186371c0e7b",
    "gen3-rs-berry-glitch-zigzagoon-0009": "a3e12bb015739bd4109ad80e3e16c198a89cb840f3d25462c9d0958dfd1af122",
}


class Trainer:
    def __init__(self, mem, profile, backup_dir):
        self.g = mem
        self.profile = profile
        self.backup_dir = Path(backup_dir)
        self.verified = False
        self.locked_boxes = set()
        # A reference belongs to this connection's Trainer, even when a later
        # connection reads the same ROM and identical PC bytes.
        self.connection_generation = uuid4().hex

    def verify(self):
        self.verified = False
        if "ROMCRC" in self.g.capabilities:
            expected = self.profile["rom_crc32"]
            if self.g.command("ROMCRC").decode("ascii").lower() != expected:
                raise ValueError("当前 ROM 的完整 CRC32 与已验证版本不同，未开放编辑")
            self.g.expected_rom_crc32 = expected
        for signature in self.profile["signatures"]:
            expected = bytes.fromhex(signature["hex"])
            if self.g.read(signature["address"], len(expected)) != expected:
                raise ValueError("当前 ROM 与已验证水银 FC 布局不匹配，未开放编辑")
        self.verified = True

    def locate(self):
        address = self.g.r32(SAVE_POINTER)
        if address % 4 or not 0x02000000 <= address <= 0x02040000 - 0x296:
            raise ValueError("存档结构指针无效，请进入游戏后再刷新")
        return address

    def snapshot(self, pocket_id=1):
        self.verify()
        sb = self.locate()
        count = self.g.r8(PARTY_COUNT)
        battle_raw = self.g.read(self.profile["battle_flag"]["address"], 1)
        if not 0 <= count <= 6:
            raise ValueError("队伍数量异常")
        party = tuple(Pokemon(self.g.read(PARTY + i * 100, 100)) for i in range(count))
        money = self.g.read(sb + 0x290, 6)
        economy = self.profile["economy"]
        for signature in economy["signatures"]:
            expected = bytes.fromhex(signature["hex"])
            if self.g.read(signature["address"], len(expected)) != expected:
                raise ValueError("当前 ROM 的代币/点数布局不匹配")
        save2 = self.g.r32(self.profile["trainer"]["pointer_address"])
        key_offset = economy["security_key_offset"]
        if save2 % 4 or not 0x02000000 <= save2 <= 0x02040000 - key_offset - 4:
            raise ValueError("金钱密钥所在训练师结构指针无效")
        key_raw = self.g.read(save2 + key_offset, 4)
        values_raw = {
            key: self.g.read(economy[key]["address"], economy[key]["size"])
            for key in ("coins", "beauty_points", "bracer_points")
        }
        pocket = self.profile["pockets"][integer(pocket_id, 1, 5, "口袋") - 1]
        bag = self.g.read(pocket["address"], pocket["capacity"] * 4)
        if self.locate() != sb or self.g.r8(PARTY_COUNT) != count:
            raise ValueError("读取时游戏数据发生变化，请刷新")
        if (
            self.g.r32(self.profile["trainer"]["pointer_address"]) != save2
            or self.g.read(save2 + key_offset, 4) != key_raw
            or self.g.read(sb + 0x290, 6) != money
            or any(
                self.g.read(economy[key]["address"], len(raw)) != raw
                for key, raw in values_raw.items()
            )
        ):
            raise ValueError("读取时代币或点数已变化，请刷新")
        return {
            "saveblock": sb,
            "money_raw": money,
            "money": struct.unpack_from("<I", money)[0]
            ^ struct.unpack("<I", key_raw)[0],
            "money_save2": save2,
            "money_key_raw": key_raw,
            "values_raw": values_raw,
            **{key: int.from_bytes(raw, "little") for key, raw in values_raw.items()},
            "party": party,
            "pocket": pocket,
            "bag": bag,
            "battle_raw": battle_raw,
            "in_battle": bool(battle_raw[0] & self.profile["battle_flag"]["mask"]),
        }

    def base_stats(self, species):
        data = self.profile["species"].get(str(species))
        if not data:
            raise ValueError("该物种种族值尚未验证")
        return data["base"]

    def snapshot_trainer(self):
        self.verify()
        layout = self.profile["trainer"]
        pointer = layout["pointer_address"]
        address = self.g.r32(pointer)
        if address % 4 or not 0x02000000 <= address <= 0x02040000 - 14:
            raise ValueError("训练师结构指针无效，请进入游戏后再读取")
        raw = self.g.read(address, 14)
        if self.g.r32(pointer) != address or self.g.read(address, 14) != raw:
            raise ValueError("读取时训练师资料已变化，请重新读取")
        tid, sid = struct.unpack_from("<HH", raw, 10)
        return {
            "address": address,
            "raw": raw,
            "tid": tid,
            "sid": sid,
            "name_raw": raw[:8].hex(),
            "name": decode_name(raw[:8]),
            "gender": raw[8],
        }

    def snapshot_time(self):
        self.verify()
        layout = self.profile["time"]
        for signature in layout["signatures"]:
            expected = bytes.fromhex(signature["hex"])
            if self.g.read(signature["address"], len(expected)) != expected:
                raise ValueError("时间相关 ROM 函数不匹配")
        pointer = self.profile["trainer"]["pointer_address"]
        for _ in range(3):
            address = self.g.r32(pointer)
            if address % 4 or not 0x02000000 <= address <= 0x02040000 - 19:
                raise ValueError("累计时长结构指针无效")
            raw = self.g.read(
                address + layout["playtime_offset"], layout["playtime_size"]
            )
            clock_raw = self.g.read(layout["clock_address"], layout["clock_size"])
            invert = self.g.r8(layout["invert_ampm_address"])
            error = self.g.read(layout["rtc_error_address"], 2)
            if (
                self.g.r32(pointer) != address
                or self.g.read(address + 14, 4) != raw[:4]
                or self.g.read(layout["clock_address"], 9) != clock_raw
            ):
                continue
            clock, mismatch = decode_game_clock(clock_raw)
            daily_raw = self.g.read(layout["daily_event"]["address"], 4)
            if self.g.read(layout["daily_event"]["address"], 4) != daily_raw:
                continue
            try:
                daily_date = decode_daily_event(daily_raw)
                daily_error = ""
            except ValueError as exc:
                daily_date, daily_error = None, str(exc)
            return {
                "address": address,
                "raw": raw,
                "playtime": decode_playtime(raw),
                "clock": clock,
                "weekday_mismatch": mismatch,
                "invert_ampm": bool(invert),
                "rtc_error": int.from_bytes(error, "little"),
                "clock_raw": clock_raw,
                "virtual_clock": bool(
                    layout.get("virtual_clock")
                    and clock_raw[2] == layout["virtual_clock"]["marker"]
                ),
                "daily_raw": daily_raw,
                "daily_date": daily_date,
                "daily_error": daily_error,
            }
        raise ValueError("读取期间时间已变化，请在游戏菜单中重新读取，保持 mGBA 运行")

    def commit_playtime(self, time_snap, hours, minutes, seconds):
        after = struct.pack(
            "<HBB",
            integer(hours, 0, 999, "累计小时"),
            integer(minutes, 0, 59, "累计分钟"),
            integer(seconds, 0, 59, "累计秒"),
        )
        decode_playtime(time_snap["raw"])
        snap = self.snapshot()
        snap["time"] = time_snap
        snap["time_apply_absolute"] = True
        return self.commit(
            snap,
            [(time_snap["address"] + 14, time_snap["raw"][:4], after)],
            "累计游玩时长",
        )

    def daily_repair_preview(self, time_snap):
        if time_snap["rtc_error"] or time_snap["weekday_mismatch"]:
            raise ValueError("请先校准 RTC 并重新读取游戏时间，再修复每日刷新日期")
        before = decode_daily_event(time_snap["daily_raw"])
        if before is None or before.date() <= time_snap["clock"].date():
            raise ValueError("每日刷新记录没有处于未来；无需执行此修复")
        return before, time_snap["clock"] - timedelta(days=1)

    def commit_daily_repair(self, time_snap):
        _, target = self.daily_repair_preview(time_snap)
        snap = self.snapshot()
        snap["daily_time"] = time_snap
        address = self.profile["time"]["daily_event"]["address"]
        return self.commit(
            snap,
            [(address, time_snap["daily_raw"], encode_daily_event(target))],
            "修复未来的每日刷新日期",
        )

    def edit_trainer_ids(self, trainer_snap, tid, sid):
        after = struct.pack(
            "<HH",
            integer(tid, 0, 65535, "玩家 TID"),
            integer(sid, 0, 65535, "玩家 SID"),
        )
        return [(trainer_snap["address"] + 10, trainer_snap["raw"][10:14], after)]

    def read_player_ot(self):
        player = self.snapshot_trainer()
        values = {
            "ot_tid": player["tid"],
            "ot_sid": player["sid"],
            "ot_gender": integer(player["gender"], 0, 1, "玩家性别"),
        }
        note = "已读取玩家资料并填入原训练师草稿；请检查预览后再写入。"
        try:
            encode_name(player["name"], 7)
        except ValueError:
            note += " 玩家姓名无法按已核对编码填入，原训练师姓名保持不变。"
        else:
            values["ot_name"] = player["name"]
        return values, note

    def commit_trainer_ids(self, trainer_snap, tid, sid):
        snap = self.snapshot()
        snap["trainer"] = trainer_snap
        return self.commit(
            snap, self.edit_trainer_ids(trainer_snap, tid, sid), "玩家训练师 ID"
        )

    def commit_trainer_profile(self, trainer_snap, tid, sid, name=None):
        snap = self.snapshot()
        snap["trainer"] = trainer_snap
        patches = self.edit_trainer_ids(trainer_snap, tid, sid)
        if name is not None:
            patches.append(
                (trainer_snap["address"], trainer_snap["raw"][:8], encode_name(name, 8))
            )
        return self.commit(snap, patches, "玩家训练师资料")

    def snapshot_box(self, box_index):
        self.verify()
        layout = self.profile.get("storage")
        if not layout:
            raise ValueError("当前配置未包含已核对的 PC 盒子结构")
        index = integer(box_index, 0, len(layout["box_addresses"]) - 1, "盒子位置")
        for signature in layout["signatures"]:
            expected = bytes.fromhex(signature["hex"])
            if self.g.read(signature["address"], len(expected)) != expected:
                raise ValueError("当前 ROM 的 PC 压缩结构不匹配")
        address = layout["box_addresses"][index]
        size = layout["slots_per_box"] * layout["record_size"]
        if (
            layout["record_size"] != 58
            or not 0x02000000 <= address <= 0x02040000 - size
        ):
            raise ValueError("PC 盒子地址或记录大小异常")
        raw = self.g.read(address, size)
        if raw != self.g.read(address, size):
            raise ValueError("读取时盒子内容已变化，请重新读取")
        return {
            "index": index,
            "address": address,
            "raw": raw,
            "pokemon": tuple(BoxPokemon(raw[i : i + 58]) for i in range(0, size, 58)),
        }

    def edit_box(self, snap, slot, **changes):
        self.check_box_unlocked(snap["index"])
        slot = integer(slot, 0, 29, "盒子位置")
        address = self.profile["storage"]["box_addresses"][snap["index"]]
        if snap["address"] != address:
            raise ValueError("盒子快照地址不一致")
        mon = snap["pokemon"][slot]
        self.check_capture_ball(mon, changes)
        self.check_met_location(mon, changes)
        updated, report = mon.edit(self.profile, **changes)
        return [(address + slot * 58, mon.raw, updated.raw)], report

    def is_box_patch(self, address, size):
        return any(
            (size == 1740 and address == a)
            or (size == 58 and a <= address < a + 30 * 58 and (address - a) % 58 == 0)
            for a in self.profile.get("storage", {}).get("box_addresses", [])
        )

    def check_box_unlocked(self, index):
        if index in self.locked_boxes:
            raise ValueError(f"第 {index + 1} 盒已锁定，请先解锁")

    def check_box_patches_unlocked(self, patches):
        for address, before, _ in patches:
            for index, base in enumerate(
                self.profile.get("storage", {}).get("box_addresses", [])
            ):
                if address < base + 1740 and address + len(before) > base:
                    self.check_box_unlocked(index)
            if len(before) == 9:
                for index in range(25):
                    if address == box_name_address(self.profile["rom_sha256"], index):
                        self.check_box_unlocked(index)

    def snapshot_box_name(self, box_index):
        self.verify()
        index = integer(box_index, 0, 24, "盒子位置")
        address = box_name_address(self.profile["rom_sha256"], index)
        pointer_address = 0x03005418
        table = {
            "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821": 0x09DD7210,
            "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896": 0x09DDEBCC,
        }[self.profile["rom_sha256"]]
        table_address = table + 4 * index
        table_raw = self.g.read(table_address, 4)
        pointer_raw = self.g.read(pointer_address, 4)
        if (
            int.from_bytes(table_raw, "little") != address
            or int.from_bytes(pointer_raw, "little") + 0x361 != 0x020315F5
        ):
            raise ValueError("盒名指针与已核验布局不匹配")
        raw = self.g.read(address, 9)
        name = decode_box_name(raw)
        if self.g.read(address, 9) != raw:
            raise ValueError("读取时盒名已变化，请重新读取")
        return {
            "index": index,
            "address": address,
            "raw": raw,
            "name": name,
            "pointer_address": pointer_address,
            "pointer_raw": pointer_raw,
            "table_address": table_address,
            "table_raw": table_raw,
        }

    def prepare_box_name(self, box_index, name):
        self.check_box_unlocked(integer(box_index, 0, 24, "盒子位置"))
        snapshot = self.snapshot_box_name(box_index)
        return {
            **snapshot,
            "after": encode_box_name(name),
            "rom_sha256": self.profile["rom_sha256"],
            "connection_generation": self.connection_generation,
        }

    def commit_box_name(self, prepared):
        if (
            prepared.get("connection_generation") != self.connection_generation
            or prepared.get("rom_sha256") != self.profile["rom_sha256"]
        ):
            raise ValueError("盒名预览来自旧连接或另一ROM，请重新预览")
        index = integer(prepared["index"], 0, 24, "盒子位置")
        self.check_box_unlocked(index)
        if prepared["address"] != box_name_address(self.profile["rom_sha256"], index):
            raise ValueError("盒名预览地址不一致")
        current = self.snapshot_box_name(index)
        if any(
            prepared[key] != current[key]
            for key in ("pointer_address", "pointer_raw", "table_address", "table_raw")
        ):
            raise ValueError("盒名布局指针已变化，请重新预览")
        decode_box_name(prepared["raw"])
        decode_box_name(prepared["after"])
        snap = self.snapshot()
        snap["box_name_guards"] = [
            (prepared["pointer_address"], prepared["pointer_raw"], prepared["pointer_raw"]),
            (prepared["table_address"], prepared["table_raw"], prepared["table_raw"]),
        ]
        return self.commit(
            snap,
            [(prepared["address"], prepared["raw"], prepared["after"])],
            f"第 {index + 1} 盒改名",
        )

    def _gift_template(self, distribution_id):
        catalog = load_distributions()
        row = next(
            (r for r in usable_rows(catalog, self.profile) if r["id"] == distribution_id),
            None,
        )
        if row is None:
            raise ValueError("该配信尚未核验适用于当前ROM，不能投放")
        template = row["template"]
        if (
            template.get("native_format") not in (
                "mercury_fc_box58", "adapted_gen3_pk3_to_mercury_fc_box58"
            )
            or template.get("sha256") != _NATIVE_GIFT_HASHES.get(distribution_id)
        ):
            raise ValueError("配信模板不在已核验的原生记录白名单")
        record = bytes.fromhex(template["native_pc_hex"])
        if hashlib.sha256(record).hexdigest() != template["sha256"]:
            raise ValueError("配信模板字节哈希不匹配")
        pokemon = BoxPokemon(record)
        if pokemon.describe(self.profile)["errors"]:
            raise ValueError("配信模板与当前ROM个体结构不匹配")
        digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return row, record, pokemon, digest

    def prepare_gift_box(self, distribution_id, box, slot=None, draft=None):
        if draft not in (None, {}):
            raise ValueError("原生配信模板只能完整原样投放，不能修改字段")
        row, record, pokemon, digest = self._gift_template(distribution_id)
        index = integer(box, 0, 24, "目标盒子")
        self.check_box_unlocked(index)
        snapshot = self.snapshot_box(index)
        if slot is None:
            slot = next(
                (
                    i for i, mon in enumerate(snapshot["pokemon"])
                    if mon.raw == b"\0" * 58
                ),
                None,
            )
            if slot is None:
                raise ValueError("目标盒子已满，请选择有空位的盒子")
        else:
            slot = integer(slot, 0, 29, "目标槽位")
        if snapshot["pokemon"][slot].raw != b"\0" * 58:
            raise ValueError("礼物目标槽必须完整全零，不能覆盖已有个体")
        address = snapshot["address"] + slot * 58
        return {
            "distribution_id": distribution_id,
            "template": row,
            "template_digest": digest,
            "record": record,
            "pokemon": pokemon,
            "destination": (index, slot),
            "patches": [(address, b"\0" * 58, record)],
            "rom_sha256": self.profile["rom_sha256"],
            "connection_generation": self.connection_generation,
        }

    def commit_gift_box(self, prepared):
        if (
            prepared.get("rom_sha256") != self.profile["rom_sha256"]
            or prepared.get("connection_generation") != self.connection_generation
        ):
            raise ValueError("礼物预览来自旧连接或另一ROM，请重新预览")
        distribution_id = prepared["distribution_id"]
        row, record, _, digest = self._gift_template(distribution_id)
        if prepared["template_digest"] != digest or prepared["template"] != row:
            raise ValueError("配信模板已变化，请重新预览")
        index, slot = prepared["destination"]
        index = integer(index, 0, 24, "目标盒子")
        slot = integer(slot, 0, 29, "目标槽位")
        self.check_box_unlocked(index)
        patch = (
            self.profile["storage"]["box_addresses"][index] + slot * 58,
            b"\0" * 58,
            record,
        )
        if prepared["record"] != record or prepared["patches"] != [patch]:
            raise ValueError("礼物预览记录已变化，请重新预览")
        return self.commit(self.snapshot(), [patch], f"配信投放 {distribution_id}")

    def prepare_create_box(self, box, draft, slot=None):
        if not isinstance(draft, dict) or "raw" in draft or "native_pc_hex" in draft:
            raise ValueError("空槽创建须使用已核验的字段创建器，不能输入原始记录")
        try:
            pokemon = create_box_pokemon(self.profile, **draft)
        except TypeError as exc:
            raise ValueError("创建草稿字段缺失或不受支持") from exc
        index = integer(box, 0, 24, "目标盒子")
        self.check_box_unlocked(index)
        snapshot = self.snapshot_box(index)
        if slot is None:
            slot = next(
                (i for i, mon in enumerate(snapshot["pokemon"])
                 if mon.raw == b"\0" * 58),
                None,
            )
            if slot is None:
                raise ValueError("目标盒子已满，请选择有空位的盒子")
        else:
            slot = integer(slot, 0, 29, "目标槽位")
        if snapshot["pokemon"][slot].raw != b"\0" * 58:
            raise ValueError("创建目标槽必须完整全零，不能覆盖已有个体")
        canonical = json.dumps(draft, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        address = snapshot["address"] + slot * 58
        return {
            "template": "verified-pc-creator-v1",
            "draft": json.loads(canonical),
            "draft_digest": digest,
            "record": pokemon.raw,
            "pokemon": pokemon,
            "destination": (index, slot),
            "patches": [(address, b"\0" * 58, pokemon.raw)],
            "rom_sha256": self.profile["rom_sha256"],
            "connection_generation": self.connection_generation,
        }

    def commit_create_box(self, prepared):
        if (
            prepared.get("rom_sha256") != self.profile["rom_sha256"]
            or prepared.get("connection_generation") != self.connection_generation
        ):
            raise ValueError("创建预览来自旧连接或另一ROM，请重新预览")
        if prepared.get("template") != "verified-pc-creator-v1":
            raise ValueError("空槽创建模板身份不匹配")
        draft = prepared["draft"]
        if not isinstance(draft, dict):
            raise ValueError("创建草稿格式错误")
        canonical = json.dumps(draft, sort_keys=True, ensure_ascii=False)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != prepared["draft_digest"]:
            raise ValueError("创建草稿已变化，请重新预览")
        pokemon = create_box_pokemon(self.profile, **draft)
        index, slot = prepared["destination"]
        index = integer(index, 0, 24, "目标盒子")
        slot = integer(slot, 0, 29, "目标槽位")
        self.check_box_unlocked(index)
        patch = (
            self.profile["storage"]["box_addresses"][index] + slot * 58,
            b"\0" * 58,
            pokemon.raw,
        )
        if prepared["record"] != pokemon.raw or prepared["patches"] != [patch]:
            raise ValueError("创建预览记录已变化，请重新预览")
        return self.commit(self.snapshot(), [patch], "PC 空槽创建")

    def _prepare_party_insertion(self, pc_record, replacement_slot):
        party_pokemon = box_to_party_pokemon(self.profile, pc_record)
        snap = self.snapshot()
        count = len(snap["party"])
        if count < 6:
            if replacement_slot is not None:
                raise ValueError("队伍尚有空位时只能追加，不能顶替现有成员")
            slot = count
            before = self.g.read(PARTY + slot * 100, 100)
            if before != b"\0" * 100:
                raise ValueError("队伍末尾空位并非全零，请先在游戏内核对")
            if self.g.read(PARTY + slot * 100, 100) != before:
                raise ValueError("读取时队伍空槽已变化，请重新预览")
            patches = [
                (PARTY + slot * 100, before, party_pokemon.raw),
                (PARTY_COUNT, bytes([count]), bytes([count + 1])),
            ]
            replacing = False
        else:
            if replacement_slot is None:
                raise ValueError("队伍已满，必须明确选择一个顶替槽位")
            slot = integer(replacement_slot, 0, 5, "顶替槽位")
            before = snap["party"][slot].raw
            patches = [(PARTY + slot * 100, before, party_pokemon.raw)]
            replacing = True
        return {
            "pc_record": pc_record,
            "record": party_pokemon.raw,
            "pokemon": BoxPokemon(pc_record),
            "party_pokemon": party_pokemon,
            "destination": ("party", slot),
            "replacing": replacing,
            "replaced": snap["party"][slot] if replacing else None,
            "party_count": count,
            "patches": patches,
            "rom_sha256": self.profile["rom_sha256"],
            "connection_generation": self.connection_generation,
        }

    def _commit_party_insertion(self, prepared, expected_pc_record, label):
        if (
            prepared.get("rom_sha256") != self.profile["rom_sha256"]
            or prepared.get("connection_generation") != self.connection_generation
        ):
            raise ValueError("队伍投放预览来自旧连接或另一ROM，请重新预览")
        party_pokemon = box_to_party_pokemon(self.profile, expected_pc_record)
        count = integer(prepared["party_count"], 0, 6, "预览队伍数量")
        if not isinstance(prepared["destination"], (tuple, list)) or len(prepared["destination"]) != 2 or prepared["destination"][0] != "party":
            raise ValueError("队伍目标格式错误")
        slot = integer(prepared["destination"][1], 0, 5, "目标队伍槽")
        snap = self.snapshot()
        if len(snap["party"]) != count:
            raise ValueError("预览后队伍数量已变化，请重新预览")
        if count < 6:
            if prepared["replacing"] or slot != count:
                raise ValueError("队伍空位目标与预览不一致")
            expected = [
                (PARTY + slot * 100, b"\0" * 100, party_pokemon.raw),
                (PARTY_COUNT, bytes([count]), bytes([count + 1])),
            ]
        else:
            if not prepared["replacing"]:
                raise ValueError("满队必须明确顶替成员")
            expected = [(PARTY + slot * 100, snap["party"][slot].raw, party_pokemon.raw)]
        if (
            prepared["pc_record"] != expected_pc_record
            or prepared["record"] != party_pokemon.raw
            or prepared["patches"] != expected
            or (
                prepared["replaced"].raw if prepared["replaced"] is not None else None
            ) != (snap["party"][slot].raw if count == 6 else None)
        ):
            raise ValueError("队伍投放目标或原值已变化，请重新预览")
        return self.commit(snap, expected, label)

    def prepare_gift_party(self, distribution_id, replacement_slot=None, draft=None):
        if draft not in (None, {}):
            raise ValueError("原生配信模板只能完整原样投放，不能修改字段")
        row, record, _, digest = self._gift_template(distribution_id)
        return {
            **self._prepare_party_insertion(record, replacement_slot),
            "distribution_id": distribution_id,
            "template": row,
            "template_digest": digest,
        }

    def commit_gift_party(self, prepared):
        distribution_id = prepared["distribution_id"]
        row, record, _, digest = self._gift_template(distribution_id)
        if prepared["template_digest"] != digest or prepared["template"] != row:
            raise ValueError("配信模板已变化，请重新预览")
        return self._commit_party_insertion(prepared, record, f"队伍配信投放 {distribution_id}")

    def prepare_create_party(self, draft, replacement_slot=None):
        if not isinstance(draft, dict) or "raw" in draft or "native_pc_hex" in draft:
            raise ValueError("队伍创建须使用已核验字段创建器，不能输入原始记录")
        try:
            pc = create_box_pokemon(self.profile, **draft)
        except TypeError as exc:
            raise ValueError("创建草稿字段缺失或不受支持") from exc
        canonical = json.dumps(draft, sort_keys=True, ensure_ascii=False)
        return {
            **self._prepare_party_insertion(pc.raw, replacement_slot),
            "template": "verified-party-creator-v1",
            "draft": json.loads(canonical),
            "draft_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }

    def commit_create_party(self, prepared):
        if prepared.get("template") != "verified-party-creator-v1":
            raise ValueError("队伍创建模板身份不匹配")
        draft = prepared["draft"]
        if not isinstance(draft, dict):
            raise ValueError("创建草稿格式错误")
        canonical = json.dumps(draft, sort_keys=True, ensure_ascii=False)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != prepared["draft_digest"]:
            raise ValueError("创建草稿已变化，请重新预览")
        pc = create_box_pokemon(self.profile, **draft)
        return self._commit_party_insertion(prepared, pc.raw, "队伍空位创建或显式顶替")

    def prepare_box_sort(self):
        """Stable global internal-species sort; opaque records stay intact."""
        indices = tuple(i for i in range(25) if i not in self.locked_boxes)
        if not indices:
            raise ValueError("全部盒子已锁定，没有可排序的盒子")
        boxes = [self.snapshot_box(i) for i in indices]
        members = []
        for box in boxes:
            for mon in box["pokemon"]:
                if not mon.species:
                    if mon.raw != b"\0" * 58:
                        raise ValueError(
                            f"第 {box['index'] + 1} 盒空槽有未知残留，未生成排序"
                        )
                elif str(mon.species) not in self.profile["species"]:
                    raise ValueError(
                        f"第 {box['index'] + 1} 盒存在未知物种 {mon.species}，未生成排序"
                    )
                else:
                    members.append(mon)
        members.sort(key=lambda mon: mon.species)
        records = [mon.raw for mon in members] + [b"\0" * 58] * (
            30 * len(boxes) - len(members)
        )
        patches = [
            (box["address"], box["raw"], b"".join(records[n * 30 : (n + 1) * 30]))
            for n, box in enumerate(boxes)
        ]
        return {
            "indices": indices,
            "locked": frozenset(self.locked_boxes),
            "patches": patches,
            "count": len(members),
        }

    def commit_box_sort(self, prepared):
        if frozenset(self.locked_boxes) != prepared["locked"]:
            raise ValueError("预览后盒锁已变化，请重新预览排序")
        self.check_box_patches_unlocked(prepared["patches"])
        snap = self.snapshot()
        snap["box_batch_guards"] = [p for p in prepared["patches"] if p[1] == p[2]]
        return self.commit(snap, prepared["patches"], "全部未锁盒按内部编号排序")

    def box_reference(self, snapshot, slot):
        slot = integer(slot, 0, 29, "盒子位置")
        index = integer(snapshot["index"], 0, 24, "盒子位置")
        if snapshot["address"] != self.profile["storage"]["box_addresses"][index]:
            raise ValueError("盒子快照地址不一致")
        raw = snapshot["raw"][slot * 58 : (slot + 1) * 58]
        if len(raw) != 58 or not BoxPokemon(raw).species:
            raise ValueError("空槽不能暂存或移动")
        return {
            "box": index,
            "slot": slot,
            "raw": raw,
            "rom_sha256": self.profile["rom_sha256"],
            "connection_generation": self.connection_generation,
        }

    def prepare_box_batch(self, references, operation, target_start=None):
        """Resolve fingerprints and build one whole-box transaction, no writes."""
        if operation not in ("move", "egg"):
            raise ValueError("未知盒子批量操作")
        unique = {}
        boxes = {}

        def read(index):
            if index not in boxes:
                boxes[index] = self.snapshot_box(index)
            return boxes[index]

        for reference in references:
            if reference.get("connection_generation") != self.connection_generation:
                raise ValueError("暂存引用来自旧连接，请重新选择")
            index = integer(reference["box"], 0, 24, "盒子位置")
            slot = integer(reference["slot"], 0, 29, "盒子位置")
            self.check_box_unlocked(index)
            if reference.get("rom_sha256") != self.profile["rom_sha256"]:
                raise ValueError("暂存引用来自另一ROM版本，请重新选择")
            raw = read(index)["raw"][slot * 58 : (slot + 1) * 58]
            if raw != reference["raw"]:
                raise ValueError("暂存引用的原槽已变化，请移除并重新暂存")
            mon = BoxPokemon(raw)
            if not mon.species or str(mon.species) not in self.profile["species"]:
                raise ValueError("空槽或未知物种不能批量操作")
            if operation == "egg" and not mon.egg:
                raise ValueError("快速生蛋仅适用于全部选中成员均为已有蛋")
            unique[(index, slot)] = reference
        if not unique:
            raise ValueError("请逐只选择至少一个非空槽")
        if len(unique) > 750:
            raise ValueError("选择数量超过25盒容量")
        refs = tuple(unique.values())
        buffers = {
            index: bytearray(snapshot["raw"]) for index, snapshot in boxes.items()
        }
        destinations = []
        if operation == "move":
            target_start = integer(target_start, 0, 24, "目标起始盒")
            self.check_box_unlocked(target_start)
            for reference in refs:
                index, slot = reference["box"], reference["slot"]
                buffers[index][slot * 58 : (slot + 1) * 58] = b"\0" * 58
            empty = []
            for index in range(target_start, 25):
                if index in self.locked_boxes:
                    continue
                if index not in buffers:
                    buffers[index] = bytearray(read(index)["raw"])
                empty.extend(
                    (index, slot)
                    for slot in range(30)
                    if buffers[index][slot * 58 : (slot + 1) * 58] == b"\0" * 58
                )
                if len(empty) >= len(refs):
                    break
            if len(empty) < len(refs):
                raise ValueError(
                    "目标起始盒及其后未锁盒的空位不足；未写入，不会覆盖其他精灵"
                )
            for reference, (index, slot) in zip(refs, empty):
                buffers[index][slot * 58 : (slot + 1) * 58] = reference["raw"]
                destinations.append((index, slot))
        else:
            for reference in refs:
                index, slot = reference["box"], reference["slot"]
                patches, _ = self.prepare_box_egg_ready(boxes[index], slot)
                buffers[index][slot * 58 : (slot + 1) * 58] = patches[0][2]
                destinations.append((index, slot))
        return {
            "operation": operation,
            "connection_generation": self.connection_generation,
            "references": refs,
            "destinations": tuple(destinations),
            "locked": frozenset(self.locked_boxes),
            "patches": [
                (boxes[index]["address"], boxes[index]["raw"], bytes(raw))
                for index, raw in sorted(buffers.items())
            ],
        }

    def commit_box_batch(self, prepared):
        if prepared.get("connection_generation") != self.connection_generation:
            raise ValueError("批量预览来自旧连接，请重新选择并预览")
        if prepared["locked"] != frozenset(self.locked_boxes):
            raise ValueError("预览后盒锁已变化，请重新预览")
        self.check_box_patches_unlocked(prepared["patches"])
        snap = self.snapshot()
        snap["box_batch_guards"] = [p for p in prepared["patches"] if p[1] == p[2]]
        label = (
            "PC 批量移动"
            if prepared["operation"] == "move"
            else "PC 已有蛋批量周期归零"
        )
        return self.commit(
            snap, prepared["patches"], f"{label}（{len(prepared['references'])}只）"
        )

    def prepare_box_egg_ready(self, snapshot, slot):
        slot = integer(slot, 0, 29, "盒子位置")
        mon = snapshot["pokemon"][slot]
        if not mon.species or not mon.egg:
            raise ValueError("快速生蛋仅适用于已有的蛋")
        return self.edit_box(snapshot, slot, friendship=0)

    def commit_box(self, patches, label):
        if not patches or not all(
            self.is_box_patch(a, len(before)) for a, before, _ in patches
        ):
            raise ValueError("不是支持的盒子编辑记录")
        return self.commit(self.snapshot(), patches, label)

    def prepare_box_move(self, source, slot, target, target_slot=None):
        layout = self.profile["storage"]
        slot = integer(slot, 0, 29, "源槽位")
        for snap in (source, target):
            self.check_box_unlocked(snap["index"])
            index = integer(
                snap["index"], 0, len(layout["box_addresses"]) - 1, "盒子位置"
            )
            if (
                snap["address"] != layout["box_addresses"][index]
                or len(snap["raw"]) != 1740
            ):
                raise ValueError("盒子快照地址或长度不一致")
        if source["address"] == target["address"]:
            raise ValueError("请选择另一个目标盒子")
        raw = source["raw"][slot * 58 : (slot + 1) * 58]
        if not BoxPokemon(raw).species:
            raise ValueError("源槽位为空，不能移动")
        if target_slot is None:
            target_slot = next(
                (
                    i
                    for i in range(30)
                    if target["raw"][i * 58 : (i + 1) * 58] == b"\0" * 58
                ),
                None,
            )
            if target_slot is None:
                raise ValueError("目标盒子没有可用的空槽")
        target_slot = integer(target_slot, 0, 29, "目标槽位")
        empty = target["raw"][target_slot * 58 : (target_slot + 1) * 58]
        if empty != b"\0" * 58:
            raise ValueError("目标槽位非空或保留了未知数据，不能覆盖")
        return [
            (source["address"] + slot * 58, raw, b"\0" * 58),
            (target["address"] + target_slot * 58, empty, raw),
        ], target_slot

    def commit_box_move(self, source, slot, target, target_slot=None):
        slot = integer(slot, 0, 29, "源槽位")
        patches, target_slot = self.prepare_box_move(source, slot, target, target_slot)
        result = self.commit_box(
            patches,
            f"PC 移动：第{source['index'] + 1}盒第{slot + 1}格→第{target['index'] + 1}盒第{target_slot + 1}格",
        )
        return {**result, "target_box": target["index"], "target_slot": target_slot}

    def edit_pokemon(self, snap, slot, **changes):
        slot = integer(slot, 0, len(snap["party"]) - 1, "队伍位置")
        mon = snap["party"][slot]
        species = integer(changes.get("species", mon.species), 1, 65535, "物种")
        species = resolve_toxtricity_form(
            mon.species, mon.pid, species, changes.get("nature")
        )
        if species != mon.species:
            changes["species"] = species
        if changes.get("minior_color") is not None:
            if mon.species not in MINIOR_SPECIES or species not in MINIOR_SPECIES:
                raise ValueError("核心颜色编辑仅适用于小陨星")
            species = MINIOR_CORES[
                integer(changes["minior_color"], 0, 6, "小陨星核心颜色")
            ]
            changes["species"] = species
        metadata = self.profile["species"].get(str(species))
        if not metadata:
            raise ValueError("此物种不在已验证的本地 ROM 名单中")
        if "held" in changes:
            held = integer(changes["held"], 0, 749, "携带道具")
            item = self.profile["items"].get(str(held))
            if held and (not item or item["pocket"] in (2, 4)):
                raise ValueError("重要道具与学习器不能作为携带道具")
        selected_ability = (
            integer(changes["ability_slot"], 0, 2, "特性槽位")
            if changes.get("ability_slot") is not None
            else 2
            if mon.ability_flag and metadata["abilities"][2]
            else mon.pid & 1
            if metadata["abilities"][1]
            else 0
        )
        resolved = resolve_held_form(
            mon.species,
            species,
            mon.held,
            integer(changes.get("held", mon.held), 0, 749, "携带道具"),
            metadata["abilities"][selected_ability],
            self.profile,
        )
        if resolved != species:
            species = resolved
            changes["species"] = species
            metadata = self.profile["species"][str(species)]
        self.check_capture_ball(mon, changes)
        self.check_met_location(mon, changes)
        updated, report = mon.edit(
            base=metadata["base"],
            growth=metadata["growth"],
            experience_tables=self.profile.get("experience_tables"),
            move_data=self.profile.get("moves"),
            abilities=metadata["abilities"],
            gender_ratio=metadata["gender_ratio"],
            egg_cycles=metadata["egg_cycles"],
            default_friendship=metadata["friendship"],
            **changes,
        )
        report = self.validate_pokemon(updated)
        if updated.pp_ups != mon.pp_ups:
            report["notes"].append(
                f"PP提升次数：{mon.pp_ups} → {updated.pp_ups}；当前PP：{mon.pp} → {updated.pp}。降低上限时仅对未另行填写的当前PP作收敛。"
            )
        if report["errors"]:
            raise ValueError("；".join(report["errors"]))
        return [(PARTY + 100 * slot, mon.raw, updated.raw)], report

    def check_capture_ball(self, mon, changes):
        if "ball" in changes:
            ball = integer(changes["ball"], 0, 255, "捕获球编号")
            item = self.profile["items"].get(str(ball))
            if ball != mon.ball and (not item or item["pocket"] != 3):
                raise ValueError("新捕获球编号须属于本改版的精灵球口袋")

    def check_met_location(self, mon, changes):
        if "met_location" in changes:
            value = integer(changes["met_location"], 0, 255, "相遇地点编号")
            if value != mon.met_location and value in self.profile.get(
                "invalid_location_ids", []
            ):
                raise ValueError(
                    "此地点编号在本ROM中对应无效名称指针，不能作为新地点；原有值可保持不变"
                )

    def validate_pokemon(self, mon):
        metadata = self.profile["species"].get(str(mon.species))
        report = mon.validate(metadata["base"] if metadata else None)
        if mon.u16(28):
            report["notes"].append(f"游戏形态还原编号为 {mon.u16(28)}，未改写此字段。")
        if mon.species in MINIOR_SPECIES:
            color = mon.pid % 7
            report["notes"].append(
                f"小陨星核心颜色：{MINIOR_COLORS[color]}（PID余数 {color}）；闪光颜色以游戏实际显示为准。"
            )
            if mon.species != MINIOR_CORES[color]:
                report["notes"].append(
                    "当前形态编号与核心颜色不同，游戏还原流程会切换到PID对应的核心。"
                )
        errors = report["errors"]
        if mon.species in TOXTRICITY_SPECIES:
            form = "高调" if mon.species == 1141 else "低调"
            report["notes"].append(
                f"颤弦蝾螈：{form}形态；修改性格时同步形态，保留特性槽位，第二普通特性的种类可能改变。"
            )
            if mon.species != toxtricity_species(mon.pid % 25):
                report["notes"].append(
                    "当前形态与性格不匹配，保持原值；游戏形态检查会按性格归一化。"
                )
        if not metadata:
            errors.append("物种不在已验证的本地 ROM 名单中")
        else:
            if 1 <= mon.level <= 100:
                tables = self.profile.get("experience_tables")
                low = experience_for_level(mon.level, metadata["growth"], tables)
                high = (
                    experience_for_level(mon.level + 1, metadata["growth"], tables) - 1
                    if mon.level < 100
                    else low
                )
                if not low <= mon.experience <= high:
                    errors.append("经验值与当前等级不一致")
            abilities = metadata["abilities"]
            slot = (
                2
                if mon.ability_flag and abilities[2]
                else (mon.pid & 1 if abilities[1] else 0)
            )
            report["ability_slot"] = slot
            if not abilities[slot]:
                errors.append("当前特性槽位无可用特性")
            if (
                held_form_family(mon.species) is not None
                and str(mon.held) in self.profile["items"]
            ):
                target = held_form_species(
                    mon.species, mon.held, abilities[slot], self.profile
                )
                report["notes"].append(
                    "已核对的持物形态在改变携带道具时同步，能力值及特性以目标形态为准。"
                )
                if target != mon.species:
                    report["notes"].append(
                        f"当前持物对应形态编号 {target}，与已保存形态不同；未改变持物/物种时保持原值。"
                    )
        item = self.profile["items"].get(str(mon.held))
        if mon.held and (not item or item["pocket"] in (2, 4)):
            errors.append("携带道具编号无效或属于重要道具/学习器")
        seen = set()
        for i, (move, pp) in enumerate(zip(mon.moves, mon.pp)):
            bonus = (mon.raw[40] >> (2 * i)) & 3
            if not move:
                if pp or bonus:
                    errors.append(f"空招式槽 {i + 1} 的 PP 或提升次数非零")
                continue
            if move in seen:
                errors.append(f"招式 {move} 重复")
            seen.add(move)
            data = self.profile.get("moves", {}).get(str(move))
            if not data:
                errors.append(f"招式 {move} 尚无已验证的 ROM 数据")
            elif pp > data["pp"] * (5 + bonus) // 5:
                errors.append(f"招式槽 {i + 1} 的 PP 超过上限")
        if not seen and not mon.egg:
            errors.append("非蛋宝可梦至少需要一个招式")
        report["structural_ok"] = not errors
        report["move_sources"] = describe_move_sources(
            mon.species, mon.level, mon.moves, self.profile
        )
        return report

    def edit_money(self, snap, money, coins):
        economy = self.profile["economy"]
        money = integer(money, 0, economy["money_maximum"], "金钱")
        coins = integer(coins, 0, economy["coins"]["maximum"], "代币")
        return [
            (
                snap["saveblock"] + 0x290,
                snap["money_raw"][:4],
                struct.pack(
                    "<I", money ^ int.from_bytes(snap["money_key_raw"], "little")
                ),
            ),
            (
                economy["coins"]["address"],
                snap["values_raw"]["coins"],
                struct.pack("<I", coins),
            ),
        ]

    def edit_values(self, snap, money, coins, beauty_points, bracer_points):
        patches = self.edit_money(snap, money, coins)
        for key, value, label in [
            ("beauty_points", beauty_points, "BeautyPoints"),
            ("bracer_points", bracer_points, "BracerPoints"),
        ]:
            field = self.profile["economy"][key]
            value = integer(value, 0, field["maximum"], label)
            patches.append(
                (
                    field["address"],
                    snap["values_raw"][key],
                    value.to_bytes(field["size"], "little"),
                )
            )
        return patches

    def sort_bag(self, snap):
        pocket = snap["pocket"]
        if (
            pocket not in self.profile["pockets"]
            or len(snap["bag"]) != pocket["capacity"] * 4
        ):
            raise ValueError("背包快照范围不一致")
        records = [snap["bag"][i : i + 4] for i in range(0, len(snap["bag"]), 4)]
        updated = b"".join(
            sorted(
                records,
                key=lambda raw: (
                    int.from_bytes(raw[:2], "little") == 0,
                    int.from_bytes(raw[:2], "little"),
                ),
            )
        )
        return [(pocket["address"], snap["bag"], updated)]

    def edit_bag(self, snap, slot, item, quantity, delete=False):
        pocket = snap["pocket"]
        slot = integer(slot, 0, pocket["capacity"] - 1, "槽位")
        old = snap["bag"][slot * 4 : slot * 4 + 4]
        if delete:
            updated = b"\0" * 4
        else:
            item = integer(item, 0, 749, "道具编号")
            metadata = self.profile["items"].get(str(item))
            if not metadata or metadata["pocket"] != pocket["id"]:
                raise ValueError("道具编号不属于当前口袋（以本地 ROM 数据为准）")
            if pocket["id"] == 2:
                old_id, old_qty = struct.unpack("<HH", old)
                quantity = old_qty if item == old_id else 1
            else:
                quantity = integer(quantity, 1, 999, "数量")
            updated = struct.pack("<HH", item, quantity)
        return [(pocket["address"] + 4 * slot, old, updated)]

    def commit(self, snap, patches, label):
        self.check_box_patches_unlocked(patches)
        self.verify()
        if any(
            address == PARTY_COUNT and before == after
            for address, before, after in patches
        ):
            raise ValueError("队伍数量不能作为无变化补丁提交")
        if not {"BATCH", "ROMCRC", "CRCBATCH"} <= self.g.capabilities:
            raise ValueError("请重新加载新版 mercury_bridge.lua 后再写入")
        if (
            "time" in snap or "daily_time" in snap
        ) and "BATCHVERIFY" not in self.g.capabilities:
            raise ValueError("时间相关写入需要重新加载 0.2.10 的 mercury_bridge.lua")
        if snap.get("time_apply_absolute"):
            # The user's inputs are absolute H:M:S. Capture the latest expected
            # value after slow ROM checks, then compare inside the callback.
            # Do not edit the frame counter, which changes on every frame.
            address = snap["time"]["address"] + 14
            before = self.g.read(address, 4)
            decode_playtime(before + b"\0")
            patches = [(address, before, patches[0][2])]
        changes = [p for p in patches if p[1] != p[2]]
        if not changes:
            return {"changed": False, "backup": None}
        count_changes = [p for p in changes if p[0] == PARTY_COUNT]
        if count_changes:
            if len(count_changes) != 1:
                raise ValueError("队伍数量补丁重复")
            _, old_count, new_count = count_changes[0]
            current_count = len(snap["party"])
            if (
                len(old_count) != 1
                or len(new_count) != 1
                or old_count[0] != current_count
                or not 0 <= new_count[0] <= 6
                or abs(new_count[0] - current_count) != 1
            ):
                raise ValueError("队伍数量变化必须为相邻且有效的一个成员")
            edge_slot = min(current_count, new_count[0])
            edge_patches = [p for p in changes if p[0] == PARTY + edge_slot * 100]
            if not any(
                address == PARTY + edge_slot * 100
                and len(before) == len(after) == 100
                and before != after
                for address, before, after in edge_patches
            ):
                raise ValueError("队伍数量变化必须与对应完整成员同事务")
            if current_count < new_count[0] and any(
                before != b"\0" * 100 for _, before, _ in edge_patches
            ):
                raise ValueError("队伍新增成员的目标槽必须全零")
            if current_count > new_count[0] and any(
                after != b"\0" * 100 for _, _, after in edge_patches
            ):
                raise ValueError("队伍数量减少时必须清空末位完整成员")
        # Compare count and pointer inside the emulator callback along with edits.
        guards = [
            (
                SAVE_POINTER,
                struct.pack("<I", snap["saveblock"]),
                struct.pack("<I", snap["saveblock"]),
            ),
        ]
        if not count_changes:
            guards.append(
                (PARTY_COUNT, bytes([len(snap["party"])]), bytes([len(snap["party"])]))
            )
        if "daily_time" in snap:
            # Guard the calendar date, not ticking seconds. The target was
            # previewed as yesterday; a day change requires a fresh preview.
            clock = snap["daily_time"]["clock_raw"][:6]
            guards.append((self.profile["time"]["clock_address"], clock, clock))
            error = struct.pack("<H", snap["daily_time"]["rtc_error"])
            guards.append((self.profile["time"]["rtc_error_address"], error, error))
            guards += [
                (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
                for s in self.profile["time"]["signatures"]
            ]
        if "time" in snap:
            guards.append(
                (
                    self.profile["trainer"]["pointer_address"],
                    struct.pack("<I", snap["time"]["address"]),
                    struct.pack("<I", snap["time"]["address"]),
                )
            )
            guards += [
                (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
                for s in self.profile["time"]["signatures"]
            ]
        if "trainer" in snap:
            guards.append(
                (
                    self.profile["trainer"]["pointer_address"],
                    struct.pack("<I", snap["trainer"]["address"]),
                    struct.pack("<I", snap["trainer"]["address"]),
                )
            )
        box_edit = any(self.is_box_patch(a, len(before)) for a, before, _ in changes)
        box_name_edit = any(
            len(before) == 9
            and any(
                a == box_name_address(self.profile["rom_sha256"], index)
                for index in range(25)
            )
            for a, before, _ in changes
        )
        economy = self.profile["economy"]
        money_edit = any(a == snap["saveblock"] + 0x290 for a, _, _ in changes)
        value_edit = money_edit or any(
            a == economy[key]["address"]
            for a, _, _ in changes
            for key in ("coins", "beauty_points", "bracer_points")
        )
        bag_sort = any(
            a == p["address"] and len(before) == p["capacity"] * 4
            for a, before, _ in changes
            for p in self.profile["pockets"]
        )
        if money_edit:
            guards += [
                (
                    self.profile["trainer"]["pointer_address"],
                    struct.pack("<I", snap["money_save2"]),
                    struct.pack("<I", snap["money_save2"]),
                ),
                (
                    snap["money_save2"] + economy["security_key_offset"],
                    snap["money_key_raw"],
                    snap["money_key_raw"],
                ),
            ]
        if value_edit:
            guards += [
                (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
                for s in economy["signatures"]
            ]
        if (
            "trainer" in snap
            or "time" in snap
            or "daily_time" in snap
            or value_edit
            or bag_sort
            or box_edit
            or box_name_edit
            or count_changes
            or any(
                a < PARTY + 600 and a + len(before) > PARTY for a, before, _ in changes
            )
        ):
            if snap["in_battle"]:
                raise ValueError(
                    "战斗中不能写入数值、排序或宝可梦/训练师修改；请结束战斗并刷新后再写入"
                )
            guards.append(
                (
                    self.profile["battle_flag"]["address"],
                    snap["battle_raw"],
                    snap["battle_raw"],
                )
            )
        if box_edit:
            guards += [
                (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
                for s in self.profile["storage"]["signatures"]
            ]
        guards += snap.get("box_batch_guards", [])
        guards += snap.get("box_name_guards", [])
        # Also reject a ROM swap between Python validation and the callback.
        guards += [
            (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
            for s in self.profile["signatures"]
        ]
        large_boxes = any(
            self.is_box_patch(a, len(b)) and len(b) == 1740 for a, b, _ in patches
        )
        if large_boxes and "BOXBATCH" not in self.g.capabilities:
            raise ValueError(
                "整盒批量操作及恢复需要重新加载支持BOXBATCH的 mercury_bridge.lua"
            )
        if (
            sum(len(before) for _, before, _ in guards + changes) > 4096
            and "BATCH8192" not in self.g.capabilities
        ):
            raise ValueError("本次事务需要重新加载本次发布的 mercury_bridge.lua")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8] + ".json"
        )
        record = {
            "schema": 1,
            "label": label,
            "rom_sha256": self.profile["rom_sha256"],
            "status": "prepared",
            "patches": [
                {"address": a, "before": b.hex(), "after": c.hex()}
                for a, b, c in changes
            ],
            "party_before": [mon.raw.hex() for mon in snap["party"]],
        }
        if money_edit:
            record["money_context"] = {
                "save2": snap["money_save2"],
                "key": snap["money_key_raw"].hex(),
            }

        def save_record():
            temp = path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp.replace(path)

        save_record()  # Fail closed when a backup cannot be written.
        try:
            self.check_box_patches_unlocked(patches)
            if large_boxes:
                self.g.batch(guards + changes, verify=True, large_boxes=True)
            elif "time" in snap or "daily_time" in snap:
                # Time may tick between network requests; read back in the same
                # emulator callback, while no game frame can advance.
                self.g.batch(guards + changes, verify=True)
            else:
                self.g.batch(guards + changes)
                for address, _, after in changes:
                    if self.g.read(address, len(after)) != after:
                        raise IOError(
                            "写后读回不一致；可能游戏正在更新数据，请先核对备份与游戏当前状态"
                        )
            record["status"] = "verified"
        except Exception as exc:
            record["status"] = "failed-or-unconfirmed"
            record["error"] = str(exc)
            save_record()
            raise IOError(f"{exc}\n备份：{path}") from exc
        save_record()
        return {"changed": True, "backup": str(path)}

    def restore(self, path):
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            record.get("schema") != 1
            or record.get("rom_sha256") != self.profile["rom_sha256"]
        ):
            raise ValueError("备份格式或 ROM 版本不匹配")
        if record.get("status") != "verified":
            raise ValueError("此备份的写入未被确认完成，不能自动恢复；请先导出诊断核对")
        snap = self.snapshot()
        patches = []
        for item in record.get("patches", []):
            address = item["address"]
            before = bytes.fromhex(item["after"])
            after = bytes.fromhex(item["before"])
            if len(before) != len(after):
                raise ValueError("备份字节长度不一致")
            valid = (
                len(before) == 100
                and PARTY <= address < PARTY + 6 * 100
                and (address - PARTY) % 100 == 0
            )
            valid = valid or (
                address == PARTY_COUNT
                and len(before) == len(after) == 1
                and 0 <= before[0] <= 6
                and 0 <= after[0] <= 6
            )
            valid = valid or (len(before) == 6 and address == snap["saveblock"] + 0x290)
            if len(before) in (4, 6) and address == snap["saveblock"] + 0x290:
                context = record.get("money_context")
                if context is None:
                    if snap["money_key_raw"] != b"\0" * 4:
                        raise ValueError("旧金钱备份没有密钥信息，无法在当前密钥下恢复")
                elif context != {
                    "save2": snap["money_save2"],
                    "key": snap["money_key_raw"].hex(),
                }:
                    raise ValueError("金钱编码密钥或训练师结构已变化，不能恢复此备份")
                valid = True
            valid = valid or any(
                address == self.profile["economy"][key]["address"]
                and len(before) == self.profile["economy"][key]["size"]
                for key in ("coins", "beauty_points", "bracer_points")
            )
            valid = valid or self.is_box_patch(address, len(before))
            valid = valid or (
                len(before) == 9
                and any(
                    address == box_name_address(self.profile["rom_sha256"], index)
                    for index in range(25)
                )
            )
            if valid and len(before) == 9:
                decode_box_name(before)
                decode_box_name(after)
                index = next(
                    index
                    for index in range(25)
                    if address == box_name_address(self.profile["rom_sha256"], index)
                )
                name_snap = self.snapshot_box_name(index)
                snap.setdefault("box_name_guards", []).extend(
                    [
                        (name_snap["pointer_address"], name_snap["pointer_raw"], name_snap["pointer_raw"]),
                        (name_snap["table_address"], name_snap["table_raw"], name_snap["table_raw"]),
                    ]
                )
            valid = valid or any(
                len(before) == 4
                and p["address"] <= address < p["address"] + p["capacity"] * 4
                and (address - p["address"]) % 4 == 0
                for p in self.profile["pockets"]
            )
            valid = valid or any(
                address == p["address"] and len(before) == p["capacity"] * 4
                for p in self.profile["pockets"]
            )
            if not valid and len(before) in (4, 8) and self.profile.get("trainer"):
                trainer_snap = self.snapshot_trainer()
                if (len(before) == 4 and address == trainer_snap["address"] + 10) or (
                    len(before) == 8 and address == trainer_snap["address"]
                ):
                    snap["trainer"] = trainer_snap
                    valid = True
            if not valid and len(before) == 4 and self.profile.get("time"):
                time_snap = self.snapshot_time()
                if address == time_snap["address"] + 14:
                    decode_playtime(after + b"\0")
                    snap["time"] = time_snap
                    valid = True
                elif address == self.profile["time"]["daily_event"]["address"]:
                    decode_daily_event(after)
                    snap["daily_time"] = time_snap
                    valid = True
            if not valid:
                raise ValueError("备份中包含不支持恢复的地址或字段")
            patches.append((address, before, after))
        limit = (
            25
            if patches
            and all(
                self.is_box_patch(a, len(b)) and len(b) == 1740 for a, b, _ in patches
            )
            else 16
        )
        if not patches or len(patches) > limit:
            raise ValueError("备份修改记录数量异常")
        count_patches = [p for p in patches if p[0] == PARTY_COUNT]
        party_patches = [
            p for p in patches
            if len(p[1]) == 100 and PARTY <= p[0] < PARTY + 600
        ]
        if count_patches:
            if len(count_patches) != 1:
                raise ValueError("备份中队伍数量补丁重复")
            _, count_before, count_after = count_patches[0]
            if (
                count_before == count_after
                or count_before[0] != len(snap["party"])
                or abs(count_before[0] - count_after[0]) != 1
                or len(party_patches) != 1
                or party_patches[0][0]
                != PARTY + min(count_before[0], count_after[0]) * 100
            ):
                raise ValueError("备份中的队伍数量与末位成员不匹配")
        elif any(
            address >= PARTY + len(snap["party"]) * 100
            for address, _, _ in party_patches
        ):
            raise ValueError("备份中的队伍槽不在当前队伍范围")
        return self.commit(snap, patches, "恢复备份 " + Path(path).name)
