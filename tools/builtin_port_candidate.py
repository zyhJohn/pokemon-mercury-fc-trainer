"""Reproduce strict in-memory port candidates, not runtime compatibility grants.

Inputs are user-selected exact baseline ROMs and the audited original UPS. No
ROM, save or UPS binary is written. The JSON report is a research artifact.
"""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import release
from tools.research_report import write_report
from ups_patch import apply_ups, encode_ups, parse_ups

TEMPLATE_SHA = "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4"
PATCH_SHA = "63d3c2cdbd70cb5821c4942b253fe3bfa4ef91d93679606b6f15e340a9e9b33c"
RELOCATIONS = {
    "1.0": ((0x08A7F96C, 0x09CEBC35, 0x09CE9875),
            (0x08A7FA98, 0x097BB0A4, 0x097BB0A0)),
    "1.2": (),
}
CANDIDATES = {
    "1.0": ("cd254162", "362beb27cd6b4f4d52470fa20f0d6fe983e8ed884f25aa13d8c84bd2a0fa3179"),
    "1.2": ("47844b96", "b26e4ba349a0d5e9a52ce4e38fdec7ef1adcbe1ffef8584e592f52fb61eeb87d"),
}


@dataclass(frozen=True)
class PortCandidate:
    version: str
    image: bytes = field(repr=False)
    ups: bytes = field(repr=False)


def identity(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "crc32": f"{zlib.crc32(data):08x}"}


def build_candidate(source: bytes, template: bytes, patch: bytes) -> PortCandidate:
    """Check exact sources, all preimages and relocations before constructing."""
    if any(not isinstance(value, bytes) for value in (source, template, patch)):
        raise TypeError("Candidate inputs must be immutable bytes")
    _, (version, _, _) = release(source)
    if version not in RELOCATIONS:
        raise ValueError("研究候选只适用于已核验1.0/1.2原版")
    if len(source) != 0x02000000 or len(template) != 0x02000000:
        raise ValueError("研究候选必须使用32 MiB原版")
    if hashlib.sha256(template).hexdigest() != TEMPLATE_SHA or hashlib.sha256(patch).hexdigest() != PATCH_SHA:
        raise ValueError("候选模板ROM或UPS身份不匹配")
    original_patch = parse_ups(patch)
    patched_template = apply_ups(template, patch)
    if apply_ups(patched_template, patch, reverse=True) != template:
        raise ValueError("模板UPS反向恢复不一致")
    # Check complete hook context and allocation, including unchanged bytes.
    for first, last in ((0x5B9D0, 0x5BA04), (0x7C803C, 0x7C8068), (0xA7A000, 0xA80000)):
        if source[first:last] != template[first:last]:
            raise ValueError("原版接入点或注入区域原像不同")
    image = bytearray(source)
    for offset, xor in original_patch.records:
        end = offset + len(xor)
        if source[offset:end] != template[offset:end]:
            raise ValueError("UPS记录原像与模板不同")
        image[offset:end] = patched_template[offset:end]
    for address, before, after in RELOCATIONS[version]:
        offset = address - 0x08000000
        if struct.unpack_from("<I", image, offset)[0] != before:
            raise ValueError("payload重定位旧值不匹配")
        struct.pack_into("<I", image, offset, after)
    target = bytes(image)
    crc, target_sha = CANDIDATES[version]
    if identity(target)["crc32"] != crc or hashlib.sha256(target).hexdigest() != target_sha:
        raise ValueError("研究候选与已复核配方不一致")
    encoded = encode_ups(source, target)
    if apply_ups(source, encoded) != target or apply_ups(target, encoded, reverse=True) != source:
        raise ValueError("候选UPS往返不一致")
    return PortCandidate(version, target, encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (args.report.suffix.lower() != ".json" or args.report.resolve() in
            {args.source.resolve(), args.template.resolve(), args.ups.resolve()}):
        raise ValueError("报告必须是与输入文件分离的JSON文件")
    source = args.source.read_bytes()
    template = args.template.read_bytes()
    patch = args.ups.read_bytes()
    candidate = build_candidate(source, template, patch)
    parsed = parse_ups(candidate.ups)
    report = {"status": "research candidate; no runtime compatibility granted",
              "version": candidate.version, "source": identity(source),
              "template": identity(template),
              "template_ups": identity(patch),
              "candidate": identity(candidate.image), "ups": identity(candidate.ups),
              "ups_crc32": parsed.patch_crc32,
              "ups_source_crc32": parsed.source_crc32, "ups_target_crc32": parsed.target_crc32,
              "relocations": RELOCATIONS[candidate.version],
              "binary_files_written": False, "patched_code_executed": False}
    write_report(args.report, report, inputs=(args.source, args.template, args.ups))
    print(json.dumps({"version": candidate.version, "candidate_crc32": parsed.target_crc32}))


if __name__ == "__main__":
    main()
