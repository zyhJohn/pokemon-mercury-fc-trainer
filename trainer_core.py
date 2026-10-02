"""Verified ROM profile, immutable read snapshots, durable backups and transactions."""

import json
import os
import struct
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from box_data import BoxPokemon
from move_sources import describe_move_sources
from name_codec import decode_name, encode_name
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

PARTY = 0x02024284
PARTY_COUNT = 0x02024029
SAVE_POINTER = 0x03005008


class Trainer:
    def __init__(self, mem, profile, backup_dir):
        self.g = mem
        self.profile = profile
        self.backup_dir = Path(backup_dir)
        self.verified = False

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
        pocket = self.profile["pockets"][integer(pocket_id, 1, 5, "口袋") - 1]
        bag = self.g.read(pocket["address"], pocket["capacity"] * 4)
        if self.locate() != sb or self.g.r8(PARTY_COUNT) != count:
            raise ValueError("读取时游戏数据发生变化，请刷新")
        return {
            "saveblock": sb,
            "money_raw": money,
            "money": struct.unpack_from("<I", money)[0],
            "coins": struct.unpack_from("<H", money, 4)[0],
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

    def edit_trainer_ids(self, trainer_snap, tid, sid):
        after = struct.pack(
            "<HH",
            integer(tid, 0, 65535, "玩家 TID"),
            integer(sid, 0, 65535, "玩家 SID"),
        )
        return [(trainer_snap["address"] + 10, trainer_snap["raw"][10:14], after)]

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
        return size == 58 and any(
            a <= address < a + 30 * 58 and (address - a) % 58 == 0
            for a in self.profile.get("storage", {}).get("box_addresses", [])
        )

    def commit_box(self, patches, label):
        if not patches or not all(
            self.is_box_patch(a, len(before)) for a, before, _ in patches
        ):
            raise ValueError("不是支持的盒子编辑记录")
        return self.commit(self.snapshot(), patches, label)

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
        money = integer(money, 0, 999999, "金钱")
        coins = integer(coins, 0, 9999, "代币")
        return [
            (
                snap["saveblock"] + 0x290,
                snap["money_raw"],
                struct.pack("<IH", money, coins),
            )
        ]

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
        self.verify()
        if not {"BATCH", "ROMCRC", "CRCBATCH"} <= self.g.capabilities:
            raise ValueError("请重新加载新版 mercury_bridge.lua 后再写入")
        changes = [p for p in patches if p[1] != p[2]]
        if not changes:
            return {"changed": False, "backup": None}
        # Compare count and pointer inside the emulator callback along with edits.
        guards = [
            (
                SAVE_POINTER,
                struct.pack("<I", snap["saveblock"]),
                struct.pack("<I", snap["saveblock"]),
            ),
            (PARTY_COUNT, bytes([len(snap["party"])]), bytes([len(snap["party"])])),
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
        if (
            "trainer" in snap
            or box_edit
            or any(
                a < PARTY + 600 and a + len(before) > PARTY for a, before, _ in changes
            )
        ):
            if snap["in_battle"]:
                raise ValueError(
                    "战斗中仅可查看宝可梦/训练师；请结束战斗并刷新后再写入"
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
        # Also reject a ROM swap between Python validation and the callback.
        guards += [
            (s["address"], bytes.fromhex(s["hex"]), bytes.fromhex(s["hex"]))
            for s in self.profile["signatures"]
        ]
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

        def save_record():
            temp = path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp.replace(path)

        save_record()  # Fail closed when a backup cannot be written.
        try:
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
                and PARTY <= address < PARTY + len(snap["party"]) * 100
                and (address - PARTY) % 100 == 0
            )
            valid = valid or (len(before) == 6 and address == snap["saveblock"] + 0x290)
            valid = valid or self.is_box_patch(address, len(before))
            valid = valid or any(
                len(before) == 4
                and p["address"] <= address < p["address"] + p["capacity"] * 4
                and (address - p["address"]) % 4 == 0
                for p in self.profile["pockets"]
            )
            if not valid and len(before) in (4, 8) and self.profile.get("trainer"):
                trainer_snap = self.snapshot_trainer()
                if (len(before) == 4 and address == trainer_snap["address"] + 10) or (
                    len(before) == 8 and address == trainer_snap["address"]
                ):
                    snap["trainer"] = trainer_snap
                    valid = True
            if not valid:
                raise ValueError("备份中包含不支持恢复的地址或字段")
            patches.append((address, before, after))
        if not patches or len(patches) > 16:
            raise ValueError("备份修改记录数量异常")
        return self.commit(snap, patches, "恢复备份 " + Path(path).name)
