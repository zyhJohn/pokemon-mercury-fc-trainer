"""Refresh the four native Mercury HOME distributions from the public wiki.

Usage: python tools/import_distributions.py
The checked-in catalogue may also contain manually reviewed external events;
those rows are retained by ID. No ROMs or save files are fetched.
"""

import base64
import hashlib
import json
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from box_data import BoxPokemon
from name_codec import decode_name
from pokemon_creation import create_box_pokemon
from pokemon_data import experience_for_level

WIKI_URL = "https://sum-light.github.io/azoth-wiki/distribution/"
OUTPUT = ROOT / "distributions.json"
GALLERY = "https://github.com/projectpokemon/EventsGallery/blob/master/"
GALLERY_RAW = "https://raw.githubusercontent.com/projectpokemon/EventsGallery/master/"
EXTERNAL = (
    {
        "id": "gen3-rsefl-10-aniv-celebi-0bf5",
        "title": "10 ANIV 时拉比（火红/叶绿适用个体）",
        "group": "火红叶绿适配",
        "path": "Released/Gen 3/ENG/10th Anniversary Celebration/Journey Across America/Celebi/RSEFL - 10 ANIV Celebi (0BF5) (ENG).pk3",
        "sha256": "3126e7129dca3ddfe47eb840d3a3d46c716554f6370e2d6c4021ea676ebdcc9b",
        "source_dex": 251,
        "source_species": 251,
        "source_moves": (246, 248, 226, 195),
        "source_held": 0,
    },
    {
        "id": "gen3-rs-berry-glitch-zigzagoon-0009",
        "title": "红宝石/蓝宝石 异色蛇纹熊（0009）",
        "group": "其他版本适配",
        "path": "Released/Gen 3/ENG/Berry Glitch Shiny Zigzagoon/RS - Berry Glitch Shiny Zigzagoon (0009) (ENG).pk3",
        "sha256": "8c4fa3185c867fad392dd2608618c4a2312ec569b51e544ba629723e3549f64e",
        "source_dex": 263,
        "source_species": 288,
        "source_moves": (33, 45, 39, 0),
        "source_held": 168,
    },
)
REFERENCE_ONLY = {
    "id": "gen3-frlg-pokepark-cacnea-f6925c85",
    "title": "火红/叶绿 ポケパーク 刺球仙人掌（资料待核）",
    "path": "Released/Gen 3/JPN/ポケパーク/Eggs 2005/JoySpot/FRLG - ポケパーク Cacnea (F6925C85) (JPN).pk3",
    "sha256": "b1e5ab448c072d8ec7368fb0c69a0d8d97811ae41594139264b6d9d0dfb57a6a",
}


class DistributionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.depth = 0
        self.card = None
        self.field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "div" and "dist-card" in classes:
            if self.card is not None:
                raise ValueError("nested distribution card")
            self.card = {"title": "", "metadata_text": "", "moves_text": "", "key": ""}
            self.depth = 1
            return
        if self.card is None:
            return
        if tag == "div":
            self.depth += 1
        if tag == "strong":
            self.field = "title"
        elif "dist-meta" in classes:
            self.field = "metadata_text"
        elif "dist-moves" in classes:
            self.field = "moves_text"
        elif tag == "code":
            self.field = "key"
        elif tag == "a" and "dist-claim" in classes:
            self.card["claim_url"] = urljoin(WIKI_URL, attrs["href"])
        elif tag == "img" and "dist-sprite" in classes:
            self.card["species_name"] = attrs["alt"]

    def handle_data(self, data):
        if self.card is not None and self.field:
            self.card[self.field] += data

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag in ("strong", "code"):
            self.field = None
        elif tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.cards.append(self.card)
                self.card = None
                self.field = None
            elif self.field in ("metadata_text", "moves_text"):
                self.field = None


def parse_wiki(raw, profiles, index):
    parser = DistributionParser()
    parser.feed(raw.decode("utf-8-sig"))
    if len(parser.cards) != 4:
        raise ValueError(f"官网配信数变为 {len(parser.cards)}，请人工核对更新")
    names = {r["name"]: r["id"] for r in index["categories"]["pokemon"]}
    moves = {r["name"]: r["id"] for r in index["categories"]["moves"]}
    ball_codes = {"究极球": 25, "高级球": 1, "梦境球": 26, "月亮球": 23}
    abilities = {r["name"]: r["id"] for r in index["categories"]["abilities"]}
    rows = []
    for card in parser.cards:
        for field in ("title", "metadata_text", "moves_text", "key", "species_name", "claim_url"):
            if not card.get(field):
                raise ValueError(f"官网卡片缺少 {field}")
        key = card["key"].strip()
        if not key.startswith("PMH1."):
            raise ValueError("未知 HOME 密钥版本")
        encoded = key.split(".", 1)[1]
        raw_record = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(raw_record) != 58:
            raise ValueError("HOME 密钥不是 58 字节盒子记录")
        record = BoxPokemon(raw_record)
        species_name = card["species_name"]
        if record.species != names[species_name] or record.nickname != species_name:
            raise ValueError(f"官网物种/昵称不匹配：{species_name}")
        move_names = [m.strip() for m in card["moves_text"].split("、")]
        if tuple(moves[n] for n in move_names) != tuple(m for m in record.moves if m):
            raise ValueError(f"官网招式不匹配：{species_name}")
        meta = card["metadata_text"].strip()
        parts = [part.strip() for part in meta.split(" · ")]
        values = dict(part.split("：", 1) for part in parts)
        ability_name = values["特性"].split("（", 1)[0]
        ball_name = values["球种"]
        if record.ot_name != values["初训家"] or record.ball != ball_codes[ball_name]:
            raise ValueError(f"官网 OT 或球种不匹配：{species_name}")
        for profile in profiles:
            result = record.describe(profile)
            if (result["errors"] or result["ability"] != abilities[ability_name]
                    or result["gender"] != ("雌性" if "♀" in card["title"] else "雄性")
                    or values["个体"] != "6V" or any(iv != 31 for iv in record.ivs)):
                raise ValueError(f"{species_name} 不匹配 {profile['name']}: {result}")
        title = card["title"].strip()
        if f"{species_name} Lv.{record.describe(profiles[0])['level']}" not in title:
            raise ValueError(f"官网等级不匹配：{species_name}")
        rows.append({
            "id": f"mercury-home-{record.species}",
            "title": title,
            "source_group": "水银 HOME",
            "source_url": WIKI_URL,
            "compatibility": "verified",
            "reason": "官网 PMH1 密钥是本改版原生 58 字节 PC 记录；两版配置解析无结构错误，物种、等级、特性、招式及 OT 与卡片一致。",
            "distribution_date": None,
            "source_fields": {
                "species_name": species_name,
                "metadata_text": meta,
                "moves_text": card["moves_text"].strip(),
                "manual_key": key,
                "claim_url": card["claim_url"],
            },
            "template": {
                "native_format": "mercury_fc_box58",
                "native_pc_hex": raw_record.hex(),
                "sha256": hashlib.sha256(raw_record).hexdigest(),
                "species": record.species,
                "level": record.describe(profiles[0])["level"],
                "moves": list(record.moves),
                "pid": record.pid,
                "otid": record.otid,
                "nickname": record.nickname,
                "ot_name": record.ot_name,
                "ball": record.ball,
                "held": record.held,
                "ivs": list(record.ivs),
            },
        })
    return rows


def parse_pk3(raw, event, profiles, index):
    """Convert one pinned, plaintext Gen 3 PK3 individual, with loss audit."""
    if len(raw) not in (80, 100):
        raise ValueError("PK3 不是 80/100 字节")
    checksum = sum(struct.unpack_from("<24H", raw, 32)) & 0xFFFF
    if checksum != struct.unpack_from("<H", raw, 28)[0]:
        raise ValueError("PK3 数据校验和错误或加密未解开")
    if raw[18] != 2 or raw[19] != 2:
        raise ValueError("仅核验英文、非蛋且无坏蛋标志的 PK3")
    if raw[27] or raw[42:44] != b"\0\0" or any(raw[62:69]) or any(raw[76:80]):
        raise ValueError("PK3 有本版无法保存的标记、华丽大赛、病毒或奖章")
    if len(raw) == 100 and raw[80:84] != b"\0\0\0\0":
        raise ValueError("PK3 队伍扩展区有尚未核验的状态")
    source_species, source_held, experience = struct.unpack_from("<HHI", raw, 32)
    source_moves = struct.unpack_from("<4H", raw, 44)
    if (source_species, source_moves, source_held) != (
        event["source_species"], event["source_moves"], event["source_held"]
    ):
        raise ValueError("PK3 个体字段与审定记录不一致")
    matching = [r for r in index["categories"]["pokemon"]
                if r["dex"] == event["source_dex"] and r["id"] == source_species]
    if len(matching) != 1:
        raise ValueError("原版物种无法唯一映射到本改版内部物种")
    move_ids = {r["id"] for r in index["categories"]["moves"]}
    if any(move and move not in move_ids for move in source_moves):
        raise ValueError("原版招式 ID 无本改版目录证据")
    if source_held:
        item = next((r for r in index["categories"]["items"] if r["id"] == source_held), None)
        if item is None or item["name"] != "枝荔果":
            raise ValueError("原版持物无本改版名称映射证据")
    nickname = decode_name(raw[8:18])
    ot_name = decode_name(raw[20:27])
    if not nickname or not ot_name:
        raise ValueError("原版昵称/OT 无法用本版字库无损表达")
    ivword = struct.unpack_from("<I", raw, 72)[0]
    if ivword & (1 << 30):
        raise ValueError("本批不转换原版蛋")
    origins = struct.unpack_from("<H", raw, 70)[0]
    fields = {
        "species": source_species,
        "pid": struct.unpack_from("<I", raw, 0)[0],
        "otid": struct.unpack_from("<I", raw, 4)[0],
        "nickname": nickname,
        "ot_name": ot_name,
        "moves": list(source_moves),
        "pp_ups": [(raw[40] >> (2 * i)) & 3 for i in range(4)],
        "ivs": [(ivword >> (5 * i)) & 31 for i in range(6)],
        "evs": list(raw[56:62]),
        "held": source_held,
        "friendship": raw[41],
        "ball": (origins >> 11) & 15,
        "met_location": raw[69],
        "met_level": origins & 127,
        "ot_gender": origins >> 15,
        "egg": False,
        "experience": experience,
    }
    source_ability_bit = ivword >> 31
    results = []
    for profile in profiles:
        metadata = profile["species"][str(source_species)]
        fields["level"] = max(
            level for level in range(1, 101)
            if experience_for_level(level, metadata["growth"], profile["experience_tables"]) <= experience
        )
        # In original Gen 3, the IV bit chooses ordinary ability 1/2; this ROM
        # additionally uses PID parity. Require the resulting ability to agree.
        fields["ability_slot"] = source_ability_bit if metadata["abilities"][1] else 0
        record = create_box_pokemon(profile, **fields)
        report = record.describe(profile)
        if report["errors"] or record.ability_flag != source_ability_bit:
            raise ValueError("本版转换后特性或结构发生变化")
        results.append(record.raw)
    if results[0] != results[1]:
        raise ValueError("V1.0/V1.2 转换结果不一致")
    native = results[0]
    source_url = GALLERY + quote(event["path"], safe="/")
    return {
        "id": event["id"],
        "title": event["title"],
        "source_group": event["group"],
        "source_url": source_url,
        "compatibility": "verified",
        "reason": "第三世代个体数据已转换为本版58字节盒子记录，两版配置均通过结构验证；原版游戏版本、当前PP及队伍即时能力值不存入本版PC，预览显示来源。此模板不等于原版神秘卡片或官方来源合法性。",
        "distribution_date": None,
        "source_fields": {
            "source_format": "gen3_pk3",
            "source_games": "RSE/FRLG" if "rsefl" in event["id"] else "RS",
            "source_game_version_code": (origins >> 7) & 15,
            "source_species_internal": source_species,
            "source_national_dex": event["source_dex"],
            "source_move_ids": list(source_moves),
            "source_held_id": source_held,
            "language": raw[18],
            "pid": fields["pid"],
            "otid": fields["otid"],
            "nickname": nickname,
            "ot_name": ot_name,
            "level": fields["level"],
            "moves": list(source_moves),
            "ball": fields["ball"],
            "met_location": fields["met_location"],
            "met_level": fields["met_level"],
            "ivs": fields["ivs"],
            "evs": fields["evs"],
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_size": len(raw),
            "conversion_losses": ["原版游戏版本", "PC不保存的当前PP与队伍即时能力值"],
        },
        "template": {
            "native_format": "adapted_gen3_pk3_to_mercury_fc_box58",
            "native_pc_hex": native.hex(),
            "sha256": hashlib.sha256(native).hexdigest(),
            **fields,
        },
    }


def import_external(profiles, index):
    rows = []
    for event in EXTERNAL:
        raw_url = GALLERY_RAW + quote(event["path"], safe="/")
        raw = urlopen(Request(raw_url, headers={"User-Agent": "MercuryFCTrainer/1"}), timeout=30).read()
        if hashlib.sha256(raw).hexdigest() != event["sha256"]:
            raise ValueError(f"外部活动档案变化：{event['id']}")
        rows.append(parse_pk3(raw, event, profiles, index))
    event = REFERENCE_ONLY
    raw_url = GALLERY_RAW + quote(event["path"], safe="/")
    raw = urlopen(Request(raw_url, headers={"User-Agent": "MercuryFCTrainer/1"}), timeout=30).read()
    if hashlib.sha256(raw).hexdigest() != event["sha256"] or len(raw) != 100:
        raise ValueError("火红/叶绿参考档案变化")
    if (sum(struct.unpack_from("<24H", raw, 32)) & 0xFFFF) != struct.unpack_from("<H", raw, 28)[0]:
        raise ValueError("火红/叶绿参考档案校验和错误")
    rows.append({
        "id": event["id"],
        "title": event["title"],
        "source_group": "火红叶绿待核",
        "source_url": GALLERY + quote(event["path"], safe="/"),
        "compatibility": "pending",
        "reason": "原版日文个体的昵称含本版姓名编码尚未核验的假名，且其来源/奖章打包位非零；不替换文字或丢弃来源位来制造可投放模板。",
        "distribution_date": None,
        "source_fields": {
            "source_format": "gen3_pk3",
            "source_games": "FRLG",
            "source_sha256": event["sha256"],
            "source_size": len(raw),
            "source_species_internal": struct.unpack_from("<H", raw, 32)[0],
            "source_moves": list(struct.unpack_from("<4H", raw, 44)),
            "language": raw[18],
            "nickname_raw_hex": raw[8:18].hex(),
            "ribbon_word": struct.unpack_from("<I", raw, 76)[0],
        },
        "template": None,
    })
    return rows


def main():
    raw = urlopen(Request(WIKI_URL, headers={"User-Agent": "MercuryFCTrainer/1"}), timeout=30).read()
    profiles = [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in ("rom_profile.json", "rom_profile_v12.json")]
    index = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    rows = parse_wiki(raw, profiles, index)
    external = import_external(profiles, index)
    catalog = {
        "schema": 1,
        "sources": {"mercury_home": {"url": WIKI_URL, "sha256": hashlib.sha256(raw).hexdigest(), "count": len(rows)}},
        "rows": rows + external,
    }
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(rows)} native distributions and retained {len(external)} external entries")


if __name__ == "__main__":
    main()
