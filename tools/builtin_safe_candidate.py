"""Compose separately reviewed payload guards into an in-memory research image.

The original UPS and original port candidates keep their own identities. This
tool never writes ROM/UPS/save binaries or registers runtime compatibility.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import release
from tools.builtin_port_candidate import (
    PATCH_SHA,
    TEMPLATE_SHA,
    build_candidate,
    identity,
)
from tools.research_report import write_report
from ups_patch import apply_ups, encode_ups, parse_ups


def apply_plans(image, *plans):
    """Validate every equal-length preimage and disjoint interval before writing."""
    if not isinstance(image, bytes):
        raise TypeError("Research image must be immutable bytes")
    changes = []
    for plan in plans:
        for address, before, after in plan:
            if not isinstance(address, int) or isinstance(address, bool):
                raise TypeError("Patch addresses must be absolute integers")
            if not isinstance(before, bytes) or not isinstance(after, bytes):
                raise TypeError("Patch preimages and replacements must be bytes")
            offset = address - 0x08000000
            if not before or len(before) != len(after):
                raise ValueError("Research patches must preserve nonzero interval lengths")
            if offset < 0 or offset + len(before) > len(image):
                raise ValueError("Research patch is outside the image")
            if image[offset:offset + len(before)] != before:
                raise ValueError("Research patch preimage mismatch")
            changes.append((address, before, after))
    changes.sort(key=lambda change: change[0])
    for left, right in pairwise(changes):
        if left[0] + len(left[1]) > right[0]:
            raise ValueError("Independent research patch plans overlap")
    output = bytearray(image)
    for address, _, after in changes:
        offset = address - 0x08000000
        output[offset:offset + len(after)] = after
    return bytes(output), tuple(changes)


@dataclass(frozen=True)
class SafeCandidate:
    version: str
    image: bytes = field(repr=False)
    ups: bytes = field(repr=False)
    base_sha256: str
    changes: tuple = field(repr=False)


def build_safe_candidate(source: bytes, template: bytes, patch: bytes) -> SafeCandidate:
    if any(not isinstance(data, bytes) for data in (source, template, patch)):
        raise TypeError("Candidate inputs must be immutable bytes")
    _, (version, _, _) = release(source)
    if version == "1.33":
        if hashlib.sha256(template).hexdigest() != TEMPLATE_SHA or source != template:
            raise ValueError("Safety research requires the exact 1.33 template")
        if hashlib.sha256(patch).hexdigest() != PATCH_SHA:
            raise ValueError("Safety research requires the audited original UPS")
        base = apply_ups(source, patch)
    else:
        base = build_candidate(source, template, patch).image
    # Each module validates the SAME exact base independently. Neither module
    # needs to accept a partially modified or unknown input identity.
    from tools.builtin_bag_guard import bag_patches
    from tools.builtin_image_guard import image_patches

    target, changes = apply_plans(base, image_patches(base), bag_patches(base))
    ups = encode_ups(source, target)
    if apply_ups(source, ups) != target or apply_ups(target, ups, reverse=True) != source:
        raise ValueError("Safety candidate UPS round trip mismatch")
    return SafeCandidate(version, target, ups, hashlib.sha256(base).hexdigest(), changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source, template, patch = (path.read_bytes() for path in (args.source, args.template, args.ups))
    candidate = build_safe_candidate(source, template, patch)
    parsed = parse_ups(candidate.ups)
    report = {"status": "safety research candidate; no runtime compatibility registered",
              "version": candidate.version, "source": identity(source),
              "template": identity(template), "template_ups": identity(patch),
              "base_sha256": candidate.base_sha256, "candidate": identity(candidate.image),
              "ups": identity(candidate.ups), "ups_source_crc32": parsed.source_crc32,
              "ups_target_crc32": parsed.target_crc32, "ups_patch_crc32": parsed.patch_crc32,
              "binary_files_written": False,
              "patches": [{"address": f"{address:08x}", "size": len(before),
                           "before_sha256": hashlib.sha256(before).hexdigest(),
                           "after_sha256": hashlib.sha256(after).hexdigest()}
                          for address, before, after in candidate.changes]}
    write_report(args.report, report, inputs=(args.source, args.template, args.ups))
    print(json.dumps({"version": candidate.version, "candidate": report["candidate"]}))


if __name__ == "__main__":
    main()
