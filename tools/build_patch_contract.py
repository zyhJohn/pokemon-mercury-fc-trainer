"""Reproduce audited patch contracts from local inputs without writing a ROM.

The approved patch list is deliberately independent of CRC recognition. Adding
another version requires auditing its own UPS, code hooks and allocated payload.
Only hashes, addresses and audit results are written to the output JSON files.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_compatibility import GUARD_LIMIT, ROM_BASE, build_manifest_entry, profile_digest
from rom_versions import load_profile, release
from tools.extract_profile import extract_for_layout
from unown_records import unown_rom_features
from ups_patch import apply_ups, parse_ups
from wild_encounter_data import wild_layout


PATCH_SCHEMA = "mercury-patched-layout-v1"
APPROVED_PATCHES = {
    "63d3c2cdbd70cb5821c4942b253fe3bfa4ef91d93679606b6f15e340a9e9b33c": {
        "id": "builtin-editor-v133",
        "name": "内置修改器补丁",
        "baseline_sha256": "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4",
        # Full allocation, including unchanged bytes, literals and padding.
        "payload_regions": [(0x08A7A000, 0x08A80000)],
        "metadata_reason": "道具13使用回调指针改变；完整重提取的数值、地址及编码配置不变",
    },
}


def _subtract(start, end, guards):
    intervals = [(start, end)]
    for guard in guards:
        left, right = guard["address"], guard["address"] + guard["size"]
        updated = []
        for a, b in intervals:
            if right <= a or left >= b:
                updated.append((a, b))
            else:
                if a < left:
                    updated.append((a, left))
                if right < b:
                    updated.append((right, b))
        intervals = updated
    return intervals


def _bytes(rom, address, size):
    return rom[address - ROM_BASE:address - ROM_BASE + size]


def build(source, patch, *, root):
    root = Path(root)
    patch_sha = hashlib.sha256(patch).hexdigest()
    if patch_sha not in APPROVED_PATCHES:
        raise ValueError("该UPS未完成独立审计，不能自动登记补丁契约")
    approved = APPROVED_PATCHES[patch_sha]
    sha, (version, _, _) = release(source)
    if sha != approved["baseline_sha256"]:
        raise ValueError("补丁与已审计的原版布局不匹配")
    ups = parse_ups(patch)
    target = apply_ups(source, patch)
    if apply_ups(target, patch, reverse=True) != source:
        raise ValueError("UPS反向恢复不一致")
    profile = load_profile(source, root=root)
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reproduced = build_manifest_entry(source, profile, catalog)
    original = json.loads((root / "rom_compatibility_manifest.json").read_text(encoding="utf-8"))["releases"][sha]
    if reproduced != original:
        raise ValueError("原版兼容契约不能由本地输入复现")
    reads = []
    extracted = extract_for_layout(target, catalog, sha,
                                   read_observer=lambda a, n: reads.append((a, n)))
    expected_values = {k: v for k, v in profile.items() if k not in ("rom_sha256", "rom_crc32")}
    actual_values = {k: v for k, v in extracted.items() if k not in ("rom_sha256", "rom_crc32")}
    if expected_values != actual_values:
        changed = [k for k in expected_values.keys() | actual_values.keys()
                   if expected_values.get(k) != actual_values.get(k)]
        raise ValueError("补丁改变了已提取配置，必须独立适配：" + ", ".join(sorted(changed)))

    # Existing transaction signatures and optional feature guards remain exact.
    features = []
    for group in (profile, profile["storage"], profile["economy"], profile["time"]):
        features += [(g["address"], bytes.fromhex(g["hex"])) for g in group["signatures"]]
    features += list(unown_rom_features(sha))
    wild = wild_layout(profile)
    features += [(g["address"], bytes.fromhex(g["expected_hex"])) for g in wild["guards"]]
    features += [(wild["generator"], bytes.fromhex(wild["original_generator"])),
                 (wild["stub_address"], bytes.fromhex(wild["original_stub"]))]
    if any(_bytes(target, a, len(b)) != b for a, b in features):
        raise ValueError("补丁与现有编辑事务、未知图腾或野生遭遇代码守卫冲突")

    entry = deepcopy(original)
    exceptions = []
    for guard in entry["guards"]:
        actual = hashlib.sha256(_bytes(target, guard["address"], guard["size"])).hexdigest()
        if guard["kind"] == "metadata" and actual != guard["sha256"]:
            exceptions.append({"address": guard["address"], "size": guard["size"],
                               "baseline_sha256": guard["sha256"], "target_sha256": actual,
                               "reason": approved["metadata_reason"]})
        guard["sha256"] = actual
    for start, end in approved["payload_regions"]:
        if not ROM_BASE <= start < end <= ROM_BASE + len(target):
            raise ValueError("已审计payload区域越界")
        for a, b in _subtract(start, end, entry["guards"]):
            for address in range(a, b, GUARD_LIMIT):
                size = min(GUARD_LIMIT, b - address)
                entry["guards"].append({"address": address, "size": size, "kind": "engine",
                                        "sha256": hashlib.sha256(_bytes(target, address, size)).hexdigest()})
    entry["guards"].sort(key=lambda g: g["address"])
    for offset, xor in ups.records:
        if _subtract(ROM_BASE + offset, ROM_BASE + offset + len(xor), entry["guards"]):
            raise ValueError("UPS写入区域未被完整保护")
    entry.update({"id": approved["id"], "name": approved["name"],
                  "patch_sha256": patch_sha, "source_crc32": ups.source_crc32,
                  "target_crc32": ups.target_crc32, "target_sha256": hashlib.sha256(target).hexdigest(),
                  "patch_crc32": ups.patch_crc32, "profile_values_unchanged": True,
                  "payload_regions": [{"address": a, "size": b - a}
                                      for a, b in approved["payload_regions"]],
                  "metadata_exceptions": exceptions,
                  "scope": "完整原版保护范围及已审计内置修改器payload；完整重提取配置相同",
                  "allowed_changes": original["allowed_changes"]})
    report = {"patch_id": entry["id"], "baseline_version": version,
              "source_crc32": ups.source_crc32, "target_crc32": ups.target_crc32,
              "patch_sha256": patch_sha, "target_sha256": entry["target_sha256"],
              "ups_records": len(ups.records), "changed_bytes": sum(len(x) for _, x in ups.records),
              "extraction_reads": len(reads), "profile_values_unchanged": True,
              "transaction_features_unchanged": len(features),
              "payload_regions": approved["payload_regions"], "metadata_exceptions": exceptions,
              "guard_count": len(entry["guards"]),
              "checked_bytes": sum(g["size"] for g in entry["guards"]),
              "profile_sha256": profile_digest(profile), "reverse_roundtrip": True}
    return entry, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    entry, report = build(args.rom.read_bytes(), args.ups.read_bytes(), root=root)
    if args.output.exists():
        data = json.loads(args.output.read_text(encoding="utf-8"))
        if data.get("schema") != PATCH_SCHEMA or not isinstance(data.get("variants"), list):
            raise ValueError("补丁契约文件格式不正确")
    else:
        data = {"schema": PATCH_SCHEMA, "variants": []}
    data["variants"] = [e for e in data["variants"] if e.get("id") != entry["id"]] + [entry]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("baseline_version", "target_crc32", "guard_count", "checked_bytes")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
