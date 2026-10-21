"""Conservative, bounded structural contracts for the three audited layouts.

This is deliberately stronger than matching a few opcodes. Extracted numeric
values remain fixed; protected bytes match either a baseline or a separately
audited patch contract. Assets outside these regions may change. The manifests
contain hashes and addresses, never ROM bytes.
"""

import hashlib
import json
import re
import zlib
from pathlib import Path


ROM_BASE = 0x08000000
ROM_SIZE = 0x02000000
READ_LIMIT = 4096
GUARD_LIMIT = 65536
MAX_GUARD_BYTES = 12 * 1024 * 1024
MANIFEST_NAME = "rom_compatibility_manifest.json"
SCHEMA = "mercury-protected-layout-v1"
PATCH_MANIFEST_NAME = "rom_patch_compatibility.json"
PATCH_SCHEMA = "mercury-patched-layout-v1"
FONT_MAPPING_RANGE = (0x087D0000, 0x088B0000)


class CompatibilityError(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.compatibility_report = report


class GuardMismatch(ValueError):
    def __init__(self, message, mismatch):
        super().__init__(message)
        self.mismatch = mismatch


def normalize_crc(crc):
    if not isinstance(crc, str) or re.fullmatch(r"[0-9a-fA-F]{8}", crc) is None:
        raise ValueError("ROM CRC32 格式异常")
    return crc.lower()


def memory_crc(memory):
    response = memory.command("ROMCRC")
    if isinstance(response, bytes):
        response = response.decode("ascii")
    return normalize_crc(response)


def profile_digest(profile):
    raw = json.dumps(profile, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1] + 32:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _subtract(intervals, excluded):
    for cut_start, cut_end in excluded:
        next_intervals = []
        for start, end in intervals:
            if end <= cut_start or start >= cut_end:
                next_intervals.append((start, end))
            else:
                if start < cut_start:
                    next_intervals.append((start, cut_start))
                if end > cut_end:
                    next_intervals.append((cut_end, end))
        intervals = next_intervals
    return intervals


def build_manifest_entry(rom, profile, catalog):
    """Reproduce one contract from an exact known ROM (research use only)."""
    from tools.extract_profile import extract
    from rom_versions import release

    sha, (version, _, _) = release(rom)
    if len(rom) != ROM_SIZE:
        raise ValueError("兼容基线必须为32 MiB ROM")
    reads = []
    extracted = extract(rom, catalog, read_observer=lambda a, n: reads.append((a, a + n)))
    if extracted != profile:
        raise ValueError("ROM重新提取结果与基线配置不一致")

    # All current native data, arithmetic, save, PC creation/form-code, clock,
    # flag/variable and name-renderer routines live in these conservative spans.
    # Protecting their entire spans retains literal pools and intervening code,
    # rather than assuming the original short opcode signatures are a closure.
    engines = [
        (ROM_BASE, 0x08400000),
        (0x09CCD000, ROM_BASE + ROM_SIZE),
        (0x08DF0000, 0x08E40000),  # journal code and its complete task table
        (0x088F5644, 0x088F5644 + 100 * 2),  # journal extra acceptance flags
        # The renderer indexes 28 rows of 256 64-byte slots in each Chinese
        # font. Their GB character order is part of name_codec's meaning, so
        # glyph payloads are protected even though ordinary sprites may change.
        FONT_MAPPING_RANGE,  # normal 0x087D0000 / small 0x08840000, 0x70000 each
    ]
    # The human-readable GBA title, revision and header checksum carry no layout
    # evidence. The BPRE game code and all other header fields remain protected.
    excluded = [(ROM_BASE + 0xA0, ROM_BASE + 0xAC),
                (ROM_BASE + 0xBC, ROM_BASE + 0xBE)]
    engines = _subtract(engines, excluded)
    metadata = _subtract(_merge(reads), engines)
    guards = []
    for kind, ranges in (("engine", engines), ("metadata", metadata)):
        for start, end in ranges:
            for address in range(start, end, GUARD_LIMIT):
                size = min(GUARD_LIMIT, end - address)
                data = rom[address - ROM_BASE:address - ROM_BASE + size]
                guards.append({"address": address, "size": size, "kind": kind,
                               "sha256": hashlib.sha256(data).hexdigest()})
    guards.sort(key=lambda item: item["address"])
    return {"baseline_sha256": sha, "baseline_version": version,
            "rom_size": len(rom), "profile_sha256": profile_digest(profile),
            "guards": guards,
            "scope": "完整基础/扩展引擎区、支线代码/旗标表、中文字体完整字形映射及全部已提取元数据；数值表变化拒绝",
            "allowed_changes": "GBA标题/修订标签，以及受保护范围外的文本或图像载荷；不保证所有文案/贴图均在范围外"}


def _validated_guards(entry):
    if not isinstance(entry, dict):
        raise ValueError("兼容契约条目格式异常")
    if entry.get("rom_size") != ROM_SIZE:
        raise ValueError("兼容契约ROM长度不正确")
    guards = entry.get("guards")
    if not isinstance(guards, list) or not 1 <= len(guards) <= 8192:
        raise ValueError("兼容契约检查数量异常")
    result = []
    total = 0
    for guard in guards:
        if not isinstance(guard, dict):
            raise ValueError("兼容契约地址、长度或指纹异常")
        address, size = guard.get("address"), guard.get("size")
        digest = guard.get("sha256")
        if (type(address) is not int or type(size) is not int
                or not 1 <= size <= GUARD_LIMIT
                or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or guard.get("kind") not in ("engine", "metadata")):
            raise ValueError("兼容契约地址、长度或指纹异常")
        total += size
        result.append((address, size, digest, guard["kind"]))
    if total > MAX_GUARD_BYTES:
        raise ValueError("兼容契约累计读取超过限制")
    return tuple(result)


def _validated_baselines(root, manifest):
    from rom_versions import RELEASES

    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("兼容检查契约格式不正确")
    result = {}
    for sha, (version, crc, filename) in RELEASES.items():
        profile = json.loads((root / filename).read_text(encoding="utf-8"))
        try:
            entry = manifest["releases"][sha]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"兼容契约缺少 V{version} 基线") from exc
        if (not isinstance(profile, dict) or not isinstance(entry, dict)
                or profile.get("rom_sha256") != sha or profile.get("rom_crc32") != crc
                or entry.get("baseline_sha256") != sha
                or entry.get("baseline_version") != version
                or entry.get("profile_sha256") != profile_digest(profile)):
            raise ValueError(f"V{version} ROM配置或兼容契约基线身份不一致")
        guards = _validated_guards(entry)
        result[sha] = (profile, entry, guards)
    return result


def _validated_patch_entries(root, manifest, baselines=None):
    """Validate provenance and complete replacement guards, never a CRC allowlist."""
    from rom_versions import RELEASES

    baselines = baselines if baselines is not None else _validated_baselines(root, manifest)
    patches = json.loads((root / PATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
    if not isinstance(patches, dict) or patches.get("schema") != PATCH_SCHEMA:
        raise ValueError("补丁兼容契约格式不正确")
    variants = patches.get("variants")
    if not isinstance(variants, list) or len(variants) > 32:
        raise ValueError("补丁兼容契约变体数量异常")
    result = {}
    for entry in variants:
        if not isinstance(entry, dict):
            raise ValueError("补丁兼容契约条目格式异常")
        patch_id = entry.get("id")
        if (not isinstance(patch_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", patch_id) is None
                or patch_id in result):
            raise ValueError("补丁兼容契约标识异常或重复")
        for field in ("name", "scope", "allowed_changes"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"补丁兼容契约缺少 {field}")
        for field in ("baseline_sha256", "profile_sha256", "patch_sha256", "target_sha256"):
            if not isinstance(entry.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", entry[field]) is None:
                raise ValueError(f"补丁兼容契约 {field} 指纹异常")
        for field in ("source_crc32", "target_crc32", "patch_crc32"):
            if not isinstance(entry.get(field), str) or re.fullmatch(r"[0-9a-f]{8}", entry[field]) is None:
                raise ValueError(f"补丁兼容契约 {field} CRC32异常")
        sha = entry["baseline_sha256"]
        if sha not in baselines:
            raise ValueError("补丁兼容契约基线身份不一致")
        profile, baseline, original_guards = baselines[sha]
        version, crc, _ = RELEASES[sha]
        if (entry.get("baseline_version") != version or entry["source_crc32"] != crc
                or entry["profile_sha256"] != profile_digest(profile)):
            raise ValueError("补丁兼容契约基线身份或配置指纹不一致")
        if entry.get("profile_values_unchanged") is not True:
            raise ValueError("补丁兼容契约缺少配置数值保持不变的审计声明")
        guards = _validated_guards(entry)
        keyed = {(address, size, kind): digest for address, size, digest, kind in guards}
        if len(keyed) != len(guards):
            raise ValueError("补丁兼容契约保护范围重复")
        exceptions = entry.get("metadata_exceptions", [])
        if not isinstance(exceptions, list) or len(exceptions) > len(original_guards):
            raise ValueError("补丁兼容契约元数据审计例外格式异常")
        audited_metadata = {}
        for exception in exceptions:
            if not isinstance(exception, dict):
                raise ValueError("补丁兼容契约元数据审计例外格式异常")
            address, size = exception.get("address"), exception.get("size")
            if (type(address) is not int or type(size) is not int
                    or not isinstance(exception.get("reason"), str) or not exception["reason"].strip()
                    or any(not isinstance(exception.get(field), str)
                           or re.fullmatch(r"[0-9a-f]{64}", exception[field]) is None
                           for field in ("baseline_sha256", "target_sha256"))):
                raise ValueError("补丁兼容契约元数据审计例外地址、指纹或说明异常")
            key = (address, size, "metadata")
            if key in audited_metadata:
                raise ValueError("补丁兼容契约元数据审计例外重复")
            audited_metadata[key] = exception
        original_keys = {(address, size, kind) for address, size, _, kind in original_guards}
        changed_metadata = set()
        for address, size, digest, kind in original_guards:
            key = (address, size, kind)
            if key not in keyed:
                raise ValueError("补丁兼容契约未完整保留基线保护范围")
            if kind == "metadata" and keyed[key] != digest:
                changed_metadata.add(key)
                exception = audited_metadata.get(key)
                if (exception is None or exception["baseline_sha256"] != digest
                        or exception["target_sha256"] != keyed[key]):
                    raise ValueError("补丁兼容契约元数据表变更缺少匹配的明确审计例外")
        if set(audited_metadata) != changed_metadata:
            raise ValueError("补丁兼容契约元数据审计例外与实际变更范围不一致")
        # Added payloads are code guards. Disallow overlaps so additions cannot
        # reclassify numeric metadata or disguise an omitted baseline interval.
        for address, size, _, kind in guards:
            if (address, size, kind) not in original_keys and kind != "engine":
                raise ValueError("补丁兼容契约新增保护范围必须为代码")
        ordered = sorted(guards)
        if any(left[0] + left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("补丁兼容契约保护范围重叠")
        regions = entry.get("payload_regions")
        if not isinstance(regions, list) or not 1 <= len(regions) <= 32:
            raise ValueError("补丁兼容契约载荷区域数量异常或缺失")
        intervals = []
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError("补丁兼容契约载荷区域格式异常")
            address, size = region.get("address"), region.get("size")
            if (type(address) is not int or type(size) is not int
                    or not 1 <= size <= ROM_SIZE
                    or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size):
                raise ValueError("补丁兼容契约载荷区域地址或长度异常")
            intervals.append((address, address + size))
        intervals.sort()
        if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
            raise ValueError("补丁兼容契约载荷区域重叠")
        for start, end in intervals:
            cursor = start
            for address, size, _, _ in ordered:
                if address + size <= cursor:
                    continue
                if address > cursor:
                    break
                cursor = address + size
                if cursor >= end:
                    break
            if cursor < end:
                raise ValueError("补丁兼容契约载荷区域未被保护范围完整覆盖")
        result[patch_id] = (profile, entry, guards)
    return result


def validate_patch_contracts(root=None):
    """Require and validate packaged patch evidence without reading a ROM."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    entries = _validated_patch_entries(root, manifest)
    return {patch_id: {"name": entry["name"], "baseline_version": entry["baseline_version"],
                       "baseline_sha256": entry["baseline_sha256"],
                       "patch_sha256": entry["patch_sha256"], "guard_count": len(guards),
                       "checked_bytes": sum(size for _, size, _, _ in guards)}
            for patch_id, (_, entry, guards) in entries.items()}


def validate_contracts(root=None):
    """Check packaged contracts and profile identities without a private ROM."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    baselines = _validated_baselines(root, manifest)
    if (root / PATCH_MANIFEST_NAME).exists():
        _validated_patch_entries(root, manifest, baselines)
    return {sha: {"baseline_version": entry["baseline_version"], "guard_count": len(guards),
                  "checked_bytes": sum(size for _, size, _, _ in guards)}
            for sha, (_, entry, guards) in baselines.items()}


def _check(read, guards):
    counts = {"engine": 0, "metadata": 0}
    for address, size, digest, kind in guards:
        raw = read(address, size)
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise ValueError(f"兼容检查读取不完整：0x{address:08X} / {size}字节")
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != digest:
            label = "代码/关键映射" if kind == "engine" else "元数据表"
            raise GuardMismatch(
                f"{label}指纹不匹配：0x{address:08X} / {size}字节；未开放编辑",
                {"address": address, "size": size, "kind": kind,
                 "expected_sha256": digest, "actual_sha256": actual_digest},
            )
        counts[kind] += size
    return counts


def _memory_reader(memory):
    def read(address, size):
        if not 0 < size <= GUARD_LIMIT or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size:
            raise ValueError("兼容检查ROM读取超出范围")
        if hasattr(memory, "read_compatible_rom"):
            return memory.read_compatible_rom(address, size)
        if hasattr(memory, "read_rom"):
            return memory.read_rom(address, size)
        parts = []
        for offset in range(0, size, READ_LIMIT):
            length = min(READ_LIMIT, size - offset)
            raw = memory.read(address + offset, length)
            if not isinstance(raw, (bytes, bytearray)) or len(raw) != length:
                raise ValueError(f"兼容检查读取不完整：0x{address + offset:08X}")
            parts.append(raw)
        return b"".join(parts)
    return read


def _signature_check(read, profile):
    for signatures in [profile["signatures"],
                       *[profile[group]["signatures"] for group in ("storage", "economy", "time")]]:
        for sig in signatures:
            raw = bytes.fromhex(sig["hex"])
            if read(sig["address"], len(raw)) != raw:
                raise ValueError(f"布局特征不匹配：0x{sig['address']:08X}")


def compatible_profile(*, rom=None, crc=None, memory=None, root):
    from rom_versions import RELEASES

    report = {"schema": SCHEMA, "candidates": [], "result": "rejected"}
    if rom is None and memory is None:
        raise CompatibilityError("当前 ROM 版本尚未验证；兼容检查需要实际ROM文件或内存读取", report)
    if rom is not None:
        if len(rom) != ROM_SIZE:
            raise CompatibilityError("兼容检查仅接受32 MiB ROM；ROM长度不匹配", report)
        rom = bytes(rom)
        actual_sha = hashlib.sha256(rom).hexdigest()
        actual_crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}"

        def read(address, size):
            if not 0 <= size <= GUARD_LIMIT or not ROM_BASE <= address <= ROM_BASE + len(rom) - size:
                raise ValueError("兼容检查ROM读取超出范围")
            return rom[address - ROM_BASE:address - ROM_BASE + size]
    else:
        actual_sha = None
        actual_crc = normalize_crc(crc)
        if memory_crc(memory) != actual_crc:
            raise CompatibilityError("ROM在兼容检查开始前已切换，请重新连接", report)
        read = _memory_reader(memory)
    report["actual_crc32"] = actual_crc
    root = Path(root)
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            raise ValueError("兼容检查契约格式不正确")
        patch_entries = (_validated_patch_entries(root, manifest)
                         if (root / PATCH_MANIFEST_NAME).exists() else {})
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
        raise CompatibilityError(f"兼容检查契约读取或校验失败：{exc}", report) from exc
    matches = []
    for sha, (version, baseline_crc, filename) in RELEASES.items():
        candidate = {"baseline_version": version, "baseline_sha256": sha}
        report["candidates"].append(candidate)
        try:
            profile = json.loads((root / filename).read_text(encoding="utf-8"))
            entry = manifest["releases"][sha]
            if (profile["rom_sha256"] != sha or profile["rom_crc32"] != baseline_crc
                    or entry.get("baseline_sha256") != sha
                    or entry.get("profile_sha256") != profile_digest(profile)):
                raise ValueError("ROM配置或兼容契约基线身份不一致")
            guards = _validated_guards(entry)
            _signature_check(read, profile)
            counts = _check(read, guards)
            candidate.update(result="matched", checked_bytes=counts)
            matches.append((profile, entry, guards, counts))
        except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
            candidate.update(result="rejected", reason=str(exc))
            if isinstance(exc, GuardMismatch):
                candidate["mismatch"] = exc.mismatch
    for patch_id, (profile, entry, guards) in patch_entries.items():
        candidate = {"baseline_version": entry["baseline_version"],
                     "baseline_sha256": entry["baseline_sha256"],
                     "patch_id": patch_id, "patch_name": entry["name"],
                     "patch_sha256": entry["patch_sha256"]}
        report["candidates"].append(candidate)
        try:
            _signature_check(read, profile)
            counts = _check(read, guards)
            candidate.update(result="matched", checked_bytes=counts)
            matches.append((profile, entry, guards, counts))
        except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
            candidate.update(result="rejected", reason=str(exc))
            if isinstance(exc, GuardMismatch):
                candidate["mismatch"] = exc.mismatch
    if rom is None and memory_crc(memory) != actual_crc:
        raise CompatibilityError("ROM在兼容检查期间已切换，请重新连接", report)
    if len(matches) != 1:
        if matches:
            reason = "ROM兼容基线匹配不唯一，未开放编辑"
        else:
            reasons = "; ".join(f"V{c['baseline_version']}: {c.get('reason', '不匹配')}" for c in report["candidates"])
            reason = "ROM没有匹配已核验结构；" + reasons
        raise CompatibilityError(reason, report)
    profile, entry, guards, counts = matches[0]
    report["result"] = "compatible"
    profile["layout_sha256"] = profile["rom_sha256"]
    profile["rom_crc32"] = actual_crc
    profile["rom_identity"] = f"sha256:{actual_sha}" if actual_sha else f"crc32:{actual_crc}"
    if actual_sha:
        profile["actual_rom_sha256"] = actual_sha
    profile["compatibility"] = {
        "name": f"结构兼容 · 基线 V{entry['baseline_version']}",
        "baseline_version": entry["baseline_version"],
        "baseline_sha256": profile["rom_sha256"], "baseline_name": profile["name"],
        "schema": SCHEMA, "scope": entry["scope"],
        "allowed_changes": entry["allowed_changes"], "checked_bytes": counts,
        "checks": report, "metadata_policy": "unchanged-required",
    }
    if "id" in entry:
        profile["compatibility"].update({
            "name": f"结构兼容 · {entry['name']} · 基线 V{entry['baseline_version']}",
            "schema": PATCH_SCHEMA, "patch_id": entry["id"], "patch_name": entry["name"],
            "profile_values_unchanged": True,
            "metadata_exceptions": entry.get("metadata_exceptions", []),
            "payload_regions": entry["payload_regions"],
            **{key: entry[key] for key in ("patch_sha256", "source_crc32", "target_crc32",
                                          "target_sha256", "patch_crc32")},
        })
    profile["_compatibility_guards"] = guards
    return profile


def runtime_compatibility_check(memory, profile, *, full=True):
    """Re-read all protected bytes; emu:checksum alone cannot detect hot patches.

    These bounded reads are not an atomic snapshot. RAM compare-before-write and
    bridge ROM identity checks still apply to the ensuing transaction.
    """
    if not profile.get("compatibility"):
        return
    expected_crc = normalize_crc(profile["rom_crc32"])
    if memory_crc(memory) != expected_crc:
        raise ValueError("ROM已切换，兼容基线需要重新识别")
    if not full:
        return
    guards = profile.get("_compatibility_guards")
    if not guards:
        raise ValueError("兼容配置缺少本次核验指纹，请重新连接")
    _check(_memory_reader(memory), guards)
    if memory_crc(memory) != expected_crc:
        raise ValueError("ROM在兼容复验期间已切换，请重新连接")
