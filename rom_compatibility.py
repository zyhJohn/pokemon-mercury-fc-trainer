"""Conservative, bounded structural contracts for the three audited layouts.

This is deliberately stronger than matching a few opcodes. Numeric tables and
the full audited engine regions must remain identical. Assets outside these
regions may change; a new layout or new numeric metadata requires a new contract.
The manifest contains hashes and addresses, never ROM bytes.
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
FONT_MAPPING_RANGE = (0x087D0000, 0x088B0000)


class CompatibilityError(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.compatibility_report = report


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
    if entry.get("rom_size") != ROM_SIZE:
        raise ValueError("兼容契约ROM长度不正确")
    guards = entry.get("guards")
    if not isinstance(guards, list) or not 1 <= len(guards) <= 8192:
        raise ValueError("兼容契约检查数量异常")
    result = []
    total = 0
    for guard in guards:
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


def validate_contracts(root=None):
    """Check packaged contracts and profile identities without a private ROM."""
    from rom_versions import RELEASES

    root = Path(root) if root is not None else Path(__file__).resolve().parent
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError("兼容检查契约格式不正确")
    result = {}
    for sha, (version, crc, filename) in RELEASES.items():
        profile = json.loads((root / filename).read_text(encoding="utf-8"))
        try:
            entry = manifest["releases"][sha]
        except KeyError as exc:
            raise ValueError(f"兼容契约缺少 V{version} 基线") from exc
        if (profile.get("rom_sha256") != sha or profile.get("rom_crc32") != crc
                or entry.get("baseline_sha256") != sha
                or entry.get("baseline_version") != version
                or entry.get("profile_sha256") != profile_digest(profile)):
            raise ValueError(f"V{version} ROM配置或兼容契约基线身份不一致")
        guards = _validated_guards(entry)
        result[sha] = {"baseline_version": version, "guard_count": len(guards),
                       "checked_bytes": sum(size for _, size, _, _ in guards)}
    return result


def _check(read, guards):
    counts = {"engine": 0, "metadata": 0}
    for address, size, digest, kind in guards:
        raw = read(address, size)
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise ValueError(f"兼容检查读取不完整：0x{address:08X} / {size}字节")
        if hashlib.sha256(raw).hexdigest() != digest:
            label = "代码/关键映射" if kind == "engine" else "元数据表"
            raise ValueError(f"{label}指纹不匹配：0x{address:08X} / {size}字节；未开放编辑")
        counts[kind] += size
    return counts


def _memory_reader(memory):
    def read(address, size):
        if not 0 < size <= GUARD_LIMIT or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size:
            raise ValueError("兼容检查ROM读取超出范围")
        if hasattr(memory, "read_rom"):
            return memory.read_rom(address, size)
        parts = []
        for offset in range(0, size, READ_LIMIT):
            length = min(READ_LIMIT, size - offset)
            raw = memory.read(address + offset, length)
            if len(raw) != length:
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
    manifest = json.loads((Path(root) / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise CompatibilityError("兼容检查契约格式不正确", report)
    matches = []
    for sha, (version, baseline_crc, filename) in RELEASES.items():
        candidate = {"baseline_version": version, "baseline_sha256": sha}
        report["candidates"].append(candidate)
        try:
            profile = json.loads((Path(root) / filename).read_text(encoding="utf-8"))
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
        except (ValueError, KeyError) as exc:
            candidate.update(result="rejected", reason=str(exc))
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
