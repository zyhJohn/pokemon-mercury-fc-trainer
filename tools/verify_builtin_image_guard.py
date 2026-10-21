"""Reproduce image/palette guard execution using user-selected local inputs.

Optional dependency: Unicorn 2.1.4. Runs isolated CPUs with synthetic RAM and
explicit BIOS/DMA service models; never connects to mGBA or writes binaries.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_SP,
)

from tools.builtin_image_guard import (
    BASE_SHA256,
    CAVE,
    CAVE_SIZE,
    SITES,
    helper_bytes,
    thumb_bl,
)
from tools.builtin_port_candidate import build_candidate, identity
from tools.builtin_safe_candidate import build_safe_candidate
from tools.research_report import write_report
from tools.verify_builtin_menu import BuiltinCPU
from tools.verify_builtin_relocations import CONTEXT, engine, word
from ups_patch import apply_ups


def verify_images(base: bytes, image: bytes) -> dict:
    """Execute the full image matrix, allowing other separately combined guards.

    ``base`` is the caller's exact verified unguarded research image. The tested
    image may also contain the independently reviewed bag guard. This function
    never asks a base-only patch planner to accept that combined image.
    """
    if not isinstance(base, bytes) or not isinstance(image, bytes):
        raise TypeError("Execution inputs must be immutable bytes")
    if (len(base) != 0x02000000 or len(image) != len(base)
            or hashlib.sha256(base).hexdigest() not in BASE_SHA256):
        raise ValueError("Expected an exact research base and equal-size guarded image")
    for site, target in SITES:
        if image[site - 0x08000000:site - 0x08000000 + 4] != thumb_bl(site, target):
            raise ValueError("Image guard call site mismatch")
    if image[CAVE - 0x08000000:CAVE - 0x08000000 + CAVE_SIZE] != helper_bytes():
        raise ValueError("Image guard full cave mismatch")

    cpu = engine(image)
    context = bytes((x * 17 + 3) & 255 for x in range(0x80))
    tables = [("image", word(image, 0x08A7FA98), CAVE, UC_ARM_REG_R5),
              ("palette", word(image, 0x08000130), CAVE + 6, UC_ARM_REG_R6),
              ("shiny_palette", word(image, 0x08000134), CAVE + 6, UC_ARM_REG_R6)]
    exhaustive = []
    for label, table, entry, reg in tables:
        digest, nonempty, empty = hashlib.sha256(), 0, 0
        for index in range(1554):
            raw = bytearray(context)
            struct.pack_into("<H", raw, 0x2C, index)
            cpu.write(CONTEXT, bytes(raw))
            cpu.cpu.reg_write(UC_ARM_REG_R3, CONTEXT)
            cpu.cpu.reg_write(reg, table)
            cpu.call(entry)
            actual = cpu.cpu.reg_read(UC_ARM_REG_R4)
            pointer = word(base, table + index * 8)
            expected = index * 8 if pointer else 0
            if actual != expected or cpu.read(CONTEXT, len(raw)) != bytes(raw):
                raise AssertionError((label, index, "helper index/context mismatch"))
            selected = word(image, table + actual)
            if selected != (pointer or word(base, table)):
                raise AssertionError((label, index, "resource pointer changed"))
            nonempty += bool(pointer)
            empty += not pointer
            digest.update(struct.pack("<III", index, actual, selected))
        exhaustive.append({"table": label, "base": f"{table:08x}", "cases": 1554,
                           "nonempty_preserved": nonempty, "null_fallback0": empty,
                           "sha256": digest.hexdigest(), "context_unchanged": True})

    direct = []
    for index in (0, 1, 25, 1293, 1294, 1543, 1544, 1553, 1554, 2048, 65535):
        for flag in (0, 1):
            c = BuiltinCPU(image, bytes(0x61000))
            ctx = bytearray(0x400)
            struct.pack_into("<H", ctx, 0x2C, index)
            ctx[0x38] = flag
            c.write(CONTEXT, ctx)
            markers = {UC_ARM_REG_R4: 0x44114411, UC_ARM_REG_R5: 0x55225522, UC_ARM_REG_R6: 0x66336633}
            for register, value in markers.items():
                c.uc.reg_write(register, value)
            c.call(0x08A7D568)
            if (c.read(CONTEXT, len(ctx)) != bytes(ctx)
                    or c.uc.reg_read(UC_ARM_REG_SP) != 0x03007E00
                    or any(c.uc.reg_read(r) != value for r, value in markers.items())):
                raise AssertionError((index, flag, "renderer context/callee ABI mismatch"))
            expected_sources = []
            for table in (tables[0][1], tables[2 if flag else 1][1]):
                pointer = word(base, table + index * 8) if index <= 1553 else 0
                expected_sources.append(pointer or word(base, table))
            actual_sources = [b["args"][0] for b in c.bios_calls if b["swi"] == 0x12]
            if actual_sources != expected_sources or c.stub_calls:
                raise AssertionError((index, flag, "renderer source/native stub mismatch"))
            direct.append({"index": index, "shiny_flag": flag, "returned": True,
                           "source_image": f"{actual_sources[0]:08x}",
                           "source_palette": f"{actual_sources[1]:08x}",
                           "context_unchanged": True, "callee_saved_and_sp_preserved": True,
                           "bios": c.bios_calls, "dma": c.dma_calls, "native_stubs": c.stub_calls,
                           "vram_sha256": hashlib.sha256(c.read(0x06010C00, 0x800)).hexdigest(),
                           "palette_sha256": hashlib.sha256(c.read(0x02037998, 32)).hexdigest()})

    navigation = []
    for initial, key, expected in ((1293, 0x80, 1294), (1544, 0x40, 1543), (1553, 0x80, 1553)):
        for flag in (0, 1):
            c = BuiltinCPU(image, bytes(0x61000))
            c.write(CONTEXT, bytes(0x400))
            c.write(CONTEXT, bytes((0x15, 1, 1)))
            c.write(CONTEXT + 0x2C, struct.pack("<H", initial))
            c.write(CONTEXT + 0x38, bytes((flag,)))
            c.w32(word(image, 0x08A7F93C), 0x02001000)
            c.write(0x02001000, b"\1")
            c.write(word(image, 0x08A7F950) + 0x30, struct.pack("<H", key))
            visited = []
            def hook(uc, address, size, data, visited=visited):
                if address in (0x08A7D572, CAVE, 0x08A7D59C, CAVE + 6, 0x08A7D5C0):
                    visited.append(f"{address:08x}")
            c.uc.hook_add(UC_HOOK_CODE, hook)
            c.call(0x08A7B1D0, 0)
            selected = struct.unpack("<H", c.read(CONTEXT + 0x2C, 2))[0]
            if selected != expected or c.stub_calls or c.uc.reg_read(UC_ARM_REG_SP) != 0x03007E00:
                raise AssertionError((initial, key, flag, "callback selected index/ABI mismatch"))
            if initial != 1553:
                expected_sources = [word(base, tables[0][1]), word(base, tables[2 if flag else 1][1])]
                actual_sources = [b["args"][0] for b in c.bios_calls if b["swi"] == 0x12][:2]
                if (f"{CAVE:08x}" not in visited or f"{CAVE + 6:08x}" not in visited
                        or actual_sources != expected_sources):
                    raise AssertionError((initial, key, flag, "callback missed image/palette guard"))
            navigation.append({"initial": initial, "key_mask": key, "shiny_flag": flag,
                               "selected": selected, "returned": True, "milestones": visited,
                               "bios": c.bios_calls, "dma": c.dma_calls, "native_stubs": c.stub_calls})

    return {"base": identity(base), "tested_image": identity(image),
            "binary_files_written": False, "runtime_compatibility_registered": False,
            "all_checks_passed": True, "native_stub_calls": 0,
            "model": "isolated Unicorn; zero synthetic RAM/hardware; existing BuiltinCPU BIOS LZ77/copy/division and immediate DMA service models",
            "scope": "all 1554 indices in image and both palettes; 22 full D568 calls; six real key callbacks from seeded active mode15/field1/editflag; no complete item-open lifecycle, real display/IRQ timing or save/reload",
            "all_physical_indices": exhaustive, "complete_D568": direct,
            "actual_key_callback": navigation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source, template, patch = (path.read_bytes() for path in (args.source, args.template, args.ups))
    candidate = build_safe_candidate(source, template, patch)
    base = (apply_ups(source, patch) if candidate.version == "1.33" else
            build_candidate(source, template, patch).image)
    if hashlib.sha256(base).hexdigest() != candidate.base_sha256:
        raise ValueError("Safe builder and execution base identity differ")
    report = {"status": "safe-candidate execution research; no runtime compatibility registered",
              "version": candidate.version, "source": identity(source),
              "template": identity(template), "template_ups": identity(patch),
              "candidate_ups_in_memory": identity(candidate.ups),
              "strict_builder_inputs_verified": True,
              "images": verify_images(base, candidate.image)}
    write_report(args.report, report, inputs=(args.source, args.template, args.ups))
    print(json.dumps({"version": candidate.version, "candidate": identity(candidate.image),
                      "table_cases": 4662, "renderer_calls": 22, "key_callbacks": 6,
                      "all_checks_passed": report["images"]["all_checks_passed"]}))


if __name__ == "__main__":
    main()
