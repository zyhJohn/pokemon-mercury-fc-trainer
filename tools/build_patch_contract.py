"""Reproduce audited patch contracts from local inputs without writing a ROM.

The approved patch list is deliberately independent of CRC recognition. Adding
another version requires auditing its own UPS, code hooks and allocated payload.
Only hashes, addresses and audit results are written to the output JSON files.
"""

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_compatibility import (
    GUARD_LIMIT,
    ROM_BASE,
    build_manifest_entry,
    profile_digest,
)
from rom_versions import load_profile, release
from tools.builtin_safe_candidate import build_safe_candidate
from tools.extract_profile import extract_for_layout
from tools.research_report import write_report
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

# Exact, independently reproduced safety variants. These are audit definitions,
# not runtime registrations or CRC-only recognition rules.
for _patch_sha, _baseline_sha, _suffix, _target_sha, _target_crc, _footer_crc in (
    (
        "8310111595206cc4b1ab011c6199cac0c134387658977d5af062e16374c003cc",
        "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821",
        "v10",
        "093a110d43d9adbd1aa4a8b2fcdc54dff0eebce8a343b8f54354dbdb5d540de3",
        "85c03f81",
        "b96b411a",
    ),
    (
        "0185857d265aa00d3465a7d29fd20d078671a17d66e3b43d49c423ff6baeb5ca",
        "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896",
        "v12",
        "2803552d5e5bb37855080bdef5116fc75f8f8064ddfd7f6b6024dd5a338739d5",
        "0f613575",
        "fb95bb58",
    ),
    (
        "f91014b7347e123dff550c03ab36eb4ace632d2b1cf581197aa7b3622554a68d",
        "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4",
        "v133",
        "440441659ee4273a3e0a7af87207a160ee5406deceef2359c991659316bcafb5",
        "19b04315",
        "95a9f1b4",
    ),
):
    APPROVED_PATCHES[_patch_sha] = {
        "id": f"builtin-editor-safe-{_suffix}",
        "name": "内置修改器安全移植（开发核验）",
        "baseline_sha256": _baseline_sha,
        "target_sha256": _target_sha,
        "target_crc32": _target_crc,
        "patch_crc32": _footer_crc,
        "payload_regions": [(0x08A7A000, 0x08A80000)],
        "transaction_feature_count": 90,
        "metadata_reason": "道具13使用回调指针改变；完整重提取的数值、地址及编码配置不变",
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
    if address < ROM_BASE or size < 0 or address - ROM_BASE + size > len(rom):
        raise ValueError("ROM保护范围或原像不足")
    return rom[address - ROM_BASE : address - ROM_BASE + size]


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
    for key in ("target_crc32", "patch_crc32"):
        if key in approved and getattr(ups, key) != approved[key]:
            raise ValueError("UPS身份与已审计的安全变体不一致：" + key)
    target = apply_ups(source, patch)
    target_sha = hashlib.sha256(target).hexdigest()
    if "target_sha256" in approved and target_sha != approved["target_sha256"]:
        raise ValueError("目标SHA-256与已审计的安全变体不一致")
    if apply_ups(target, patch, reverse=True) != source:
        raise ValueError("UPS反向恢复不一致")
    profile = load_profile(source, root=root)
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    reproduced = build_manifest_entry(source, profile, catalog)
    original = json.loads(
        (root / "rom_compatibility_manifest.json").read_text(encoding="utf-8")
    )["releases"][sha]
    if reproduced != original:
        raise ValueError("原版兼容契约不能由本地输入复现")
    reads = []
    extracted = extract_for_layout(
        target, catalog, sha, read_observer=lambda a, n: reads.append((a, n))
    )
    expected_values = {
        k: v for k, v in profile.items() if k not in ("rom_sha256", "rom_crc32")
    }
    actual_values = {
        k: v for k, v in extracted.items() if k not in ("rom_sha256", "rom_crc32")
    }
    if expected_values != actual_values:
        changed = [
            k
            for k in expected_values.keys() | actual_values.keys()
            if expected_values.get(k) != actual_values.get(k)
        ]
        raise ValueError(
            "补丁改变了已提取配置，必须独立适配：" + ", ".join(sorted(changed))
        )

    # Existing transaction signatures and optional feature guards remain exact.
    features = []
    for group in (profile, profile["storage"], profile["economy"], profile["time"]):
        features += [
            (g["address"], bytes.fromhex(g["hex"])) for g in group["signatures"]
        ]
    features += list(unown_rom_features(sha))
    wild = wild_layout(profile)
    features += [
        (g["address"], bytes.fromhex(g["expected_hex"])) for g in wild["guards"]
    ]
    features += [
        (wild["generator"], bytes.fromhex(wild["original_generator"])),
        (wild["stub_address"], bytes.fromhex(wild["original_stub"])),
    ]
    if (
        "transaction_feature_count" in approved
        and len(features) != approved["transaction_feature_count"]
    ):
        raise ValueError("事务守卫数量与已审计的安全变体不一致")
    if any(_bytes(target, a, len(b)) != b for a, b in features):
        raise ValueError("补丁与现有编辑事务、未知图腾或野生遭遇代码守卫冲突")

    entry = deepcopy(original)
    exceptions = []
    for guard in entry["guards"]:
        actual = hashlib.sha256(
            _bytes(target, guard["address"], guard["size"])
        ).hexdigest()
        if guard["kind"] == "metadata" and actual != guard["sha256"]:
            exceptions.append(
                {
                    "address": guard["address"],
                    "size": guard["size"],
                    "baseline_sha256": guard["sha256"],
                    "target_sha256": actual,
                    "reason": approved["metadata_reason"],
                }
            )
        guard["sha256"] = actual
    for start, end in approved["payload_regions"]:
        if not ROM_BASE <= start < end <= ROM_BASE + len(target):
            raise ValueError("已审计payload区域越界")
        for a, b in _subtract(start, end, entry["guards"]):
            for address in range(a, b, GUARD_LIMIT):
                size = min(GUARD_LIMIT, b - address)
                entry["guards"].append(
                    {
                        "address": address,
                        "size": size,
                        "kind": "engine",
                        "sha256": hashlib.sha256(
                            _bytes(target, address, size)
                        ).hexdigest(),
                    }
                )
    entry["guards"].sort(key=lambda g: g["address"])
    for offset, xor in ups.records:
        if _subtract(ROM_BASE + offset, ROM_BASE + offset + len(xor), entry["guards"]):
            raise ValueError("UPS写入区域未被完整保护")
    entry.update(
        {
            "id": approved["id"],
            "name": approved["name"],
            "patch_sha256": patch_sha,
            "source_crc32": ups.source_crc32,
            "target_crc32": ups.target_crc32,
            "target_sha256": target_sha,
            "patch_crc32": ups.patch_crc32,
            "profile_values_unchanged": True,
            "payload_regions": [
                {"address": a, "size": b - a} for a, b in approved["payload_regions"]
            ],
            "metadata_exceptions": exceptions,
            "scope": "完整原版保护范围及已审计内置修改器payload；完整重提取配置相同",
            "allowed_changes": original["allowed_changes"],
        }
    )
    report = {
        "patch_id": entry["id"],
        "baseline_version": version,
        "baseline_sha256": sha,
        "source_crc32": ups.source_crc32,
        "target_crc32": ups.target_crc32,
        "patch_sha256": patch_sha,
        "patch_crc32": ups.patch_crc32,
        "target_sha256": entry["target_sha256"],
        "ups_records": len(ups.records),
        "changed_bytes": sum(len(x) for _, x in ups.records),
        "extraction_reads": len(reads),
        "profile_values_unchanged": True,
        "transaction_features_unchanged": len(features),
        "payload_regions": approved["payload_regions"],
        "metadata_exceptions": exceptions,
        "guard_count": len(entry["guards"]),
        "checked_bytes": sum(g["size"] for g in entry["guards"]),
        "profile_sha256": profile_digest(profile),
        "reverse_roundtrip": True,
    }
    return entry, report


def build_safe(source: bytes, template: bytes, original_ups: bytes, *, root):
    """Compose an audited safety UPS in memory, then reproduce its full contract."""
    candidate = build_safe_candidate(source, template, original_ups)
    entry, report = build(source, candidate.ups, root=root)
    if not entry["id"].startswith("builtin-editor-safe-"):
        raise ValueError("安全配方未匹配已审计的安全UPS身份")
    return entry, report


def _validate_outputs(outputs, inputs):
    """Preflight every destination before any destination can be mutated."""
    existing = list(map(Path, inputs))
    for destination in outputs:
        destination = Path(destination)
        if destination.suffix.lower() != ".json":
            raise ValueError("契约和报告必须使用JSON文件")
        for original in existing:
            if destination.resolve() == original.resolve() or (
                destination.exists()
                and original.exists()
                and destination.samefile(original)
            ):
                raise ValueError("输出不能覆盖输入文件或其他输出及其链接")
        existing.append(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument(
        "--safe-template",
        type=Path,
        help="安全配方使用的原版1.33模板；位置参数ups仍为原始审计UPS",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.output and not args.report:
        parser.error("至少需要--output或--report")
    inputs = (args.rom, args.ups) + (
        (args.safe_template,) if args.safe_template else ()
    )
    outputs = [path for path in (args.output, args.report) if path is not None]
    _validate_outputs(outputs, inputs)
    root = Path(__file__).resolve().parents[1]
    if args.safe_template:
        entry, report = build_safe(
            args.rom.read_bytes(),
            args.safe_template.read_bytes(),
            args.ups.read_bytes(),
            root=root,
        )
    else:
        entry, report = build(args.rom.read_bytes(), args.ups.read_bytes(), root=root)
    if args.output:
        if args.output.exists():
            data = json.loads(args.output.read_text(encoding="utf-8"))
            if data.get("schema") != PATCH_SCHEMA or not isinstance(
                data.get("variants"), list
            ):
                raise ValueError("补丁契约文件格式不正确")
        else:
            data = {"schema": PATCH_SCHEMA, "variants": []}
        data["variants"] = [
            e for e in data["variants"] if e.get("id") != entry["id"]
        ] + [entry]
        write_report(
            args.output,
            data,
            inputs=(*inputs, *((args.report,) if args.report else ())),
        )
    if args.report:
        write_report(
            args.report,
            report,
            inputs=(*inputs, *((args.output,) if args.output else ())),
        )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "baseline_version",
                    "target_crc32",
                    "guard_count",
                    "checked_bytes",
                )
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
