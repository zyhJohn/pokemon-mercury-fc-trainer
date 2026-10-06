"""Offline, evidence-backed distribution catalogue for Mercury FC."""

import hashlib
import json
from pathlib import Path

from box_data import BoxPokemon

CATALOG_PATH = Path(__file__).with_name("distributions.json")
PROFILES = ("rom_profile.json", "rom_profile_v12.json")


def load_distributions(path=CATALOG_PATH):
    with open(path, encoding="utf-8") as stream:
        catalog = json.load(stream)
    if catalog.get("schema") != 1 or not isinstance(catalog.get("rows"), list):
        raise ValueError("不支持的配信目录格式")
    seen = set()
    for row in catalog["rows"]:
        if row["id"] in seen:
            raise ValueError(f"重复的配信 ID：{row['id']}")
        seen.add(row["id"])
        if row["compatibility"] not in ("verified", "adaptable", "unsupported", "pending"):
            raise ValueError(f"未知兼容状态：{row['id']}")
        template = row.get("template")
        if row["compatibility"] == "verified" and not template:
            raise ValueError(f"已核验配信没有模板：{row['id']}")
        if template and template.get("native_pc_hex"):
            raw = bytes.fromhex(template["native_pc_hex"])
            if len(raw) != 58:
                raise ValueError(f"配信原生记录长度错误：{row['id']}")
            if hashlib.sha256(raw).hexdigest() != template["sha256"]:
                raise ValueError(f"配信原生记录哈希错误：{row['id']}")
    return catalog


def usable_rows(catalog, profile):
    """Return only rows whose native records validate against the selected ROM."""
    result = []
    for row in catalog["rows"]:
        if row["compatibility"] != "verified":
            continue
        template = row["template"]
        if "native_pc_hex" not in template:
            continue
        record = BoxPokemon(bytes.fromhex(template["native_pc_hex"]))
        if record.describe(profile)["errors"]:
            continue
        result.append(row)
    return result
