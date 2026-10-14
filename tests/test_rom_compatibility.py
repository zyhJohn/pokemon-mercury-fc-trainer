import hashlib
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from rom_compatibility import (
    CompatibilityError, FONT_MAPPING_RANGE, MANIFEST_NAME, ROM_BASE, ROM_SIZE, SCHEMA, profile_digest,
    validate_contracts,
)
from rom_versions import load_profile, rom_identity, runtime_compatibility_check


class RomMemory:
    def __init__(self, rom):
        self.rom = bytearray(rom)
        self.crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}"
        self.reads = []
        self.on_read = None

    def command(self, command):
        if command != "ROMCRC":
            raise AssertionError(command)
        return self.crc.encode("ascii")

    def read(self, address, size):
        if not 0 < size <= 4096 or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size:
            raise AssertionError("unbounded ROM read")
        self.reads.append((address, size))
        if self.on_read:
            self.on_read()
        return bytes(self.rom[address - ROM_BASE:address - ROM_BASE + size])


class LargeRomMemory(RomMemory):
    def read_rom(self, address, size):
        if not 0 < size <= 65536 or not ROM_BASE <= address <= ROM_BASE + ROM_SIZE - size:
            raise AssertionError("unbounded large ROM read")
        self.reads.append((address, size))
        return bytes(self.rom[address - ROM_BASE:address - ROM_BASE + size])


class StructuralCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.roms = []
        self.releases = {}
        self.manifest = {"schema": SCHEMA, "releases": {}}
        for index, version in enumerate(("1.0", "1.2", "1.33")):
            rom = bytearray(ROM_SIZE)
            rom[0xAC:0xB0] = b"BPRE"
            rom[0x100:0x104] = bytes([index + 1]) * 4
            rom[0x40000:0x40004] = bytes([25, 35, 45, 55])
            sha = hashlib.sha256(rom).hexdigest()
            crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}"
            filename = f"profile_{index}.json"
            profile = {
                "name": f"verified {version}", "rom_sha256": sha, "rom_crc32": crc,
                "signatures": [{"address": ROM_BASE + 0x100,
                                "hex": bytes(rom[0x100:0x104]).hex()}],
                "species": {"1": {"base": [25, 35, 45, 55, 65, 75]}},
                "moves": {"1": {"pp": 35}},
                "storage": {"signatures": []}, "economy": {"signatures": []},
                "time": {"signatures": []},
            }
            (self.root / filename).write_text(json.dumps(profile), encoding="utf-8")
            guards = []
            for start, end, kind in [(0, 0xA0, "engine"), (0xAC, 0xBC, "engine"),
                                     (0xBE, 0x10000, "engine"),
                                     (0x40000, 0x40004, "metadata")]:
                guards.append({"address": ROM_BASE + start, "size": end - start,
                               "kind": kind, "sha256": hashlib.sha256(rom[start:end]).hexdigest()})
            self.manifest["releases"][sha] = {
                "baseline_sha256": sha, "baseline_version": version,
                "rom_size": ROM_SIZE, "profile_sha256": profile_digest(profile),
                "guards": guards, "scope": "synthetic protected engine and numeric table",
                "allowed_changes": "header label and synthetic image payload",
            }
            self.releases[sha] = (version, crc, filename)
            self.roms.append(rom)
        self.write_manifest()
        active = patch.dict("rom_versions.RELEASES", self.releases, clear=True)
        active.start()
        self.addCleanup(active.stop)

    def write_manifest(self):
        (self.root / MANIFEST_NAME).write_text(json.dumps(self.manifest), encoding="utf-8")

    def variant(self, index=0):
        rom = bytearray(self.roms[index])
        rom[0xA0:0xAC] = b"MERCURY V1.1"
        rom[0xBC:0xBE] = b"\x99\x88"
        rom[0x90000:0x90004] = b"art!"
        return rom

    def test_header_and_assets_accept_each_baseline_without_guessing_version(self):
        for index, version in enumerate(("1.0", "1.2", "1.33")):
            with self.subTest(baseline=version):
                rom = self.variant(index)
                profile = load_profile(rom, root=self.root)
                self.assertEqual(profile["compatibility"]["baseline_version"], version)
                self.assertEqual(profile["rom_sha256"], list(self.releases)[index])
                self.assertEqual(profile["actual_rom_sha256"], hashlib.sha256(rom).hexdigest())
                self.assertEqual(rom_identity(profile), "sha256:" + hashlib.sha256(rom).hexdigest())
                self.assertEqual(profile["rom_crc32"], f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}")
                self.assertEqual(profile["moves"]["1"]["pp"], 35)

    def test_code_changed_outside_short_signature_refuses(self):
        rom = self.variant()
        rom[0x500] ^= 1
        with self.assertRaisesRegex(CompatibilityError, "代码/关键映射"):
            load_profile(rom, root=self.root)

    def test_pointer_signature_corruption_refuses(self):
        rom = self.variant()
        rom[0x100] = 255
        with self.assertRaisesRegex(CompatibilityError, "布局特征"):
            load_profile(rom, root=self.root)

    def test_numeric_metadata_changes_refuse_instead_of_reusing_old_values(self):
        rom = self.variant()
        rom[0x40000] += 1
        with self.assertRaisesRegex(CompatibilityError, "元数据表"):
            load_profile(rom, root=self.root)

    def test_online_unknown_crc_requires_real_read_and_keeps_actual_identity(self):
        memory = RomMemory(self.variant(1))
        profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
        self.assertEqual(profile["compatibility"]["baseline_version"], "1.2")
        self.assertEqual(rom_identity(profile), "crc32:" + memory.crc)
        self.assertNotIn("actual_rom_sha256", profile)
        self.assertTrue(memory.reads)
        self.assertTrue(all(size <= 4096 for _, size in memory.reads))

    def test_large_rom_reader_reduces_requests_without_changing_contract(self):
        old, new = RomMemory(self.variant()), LargeRomMemory(self.variant())
        a = load_profile(crc=old.crc, memory=old, root=self.root)
        b = load_profile(crc=new.crc, memory=new, root=self.root)
        self.assertEqual(a, b)
        self.assertLess(len(new.reads), len(old.reads))
        self.assertGreater(max(size for _, size in new.reads), 4096)

    def test_unknown_crc_alone_remains_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, "实际ROM"):
            load_profile(crc="00000000", root=self.root)

    def test_rom_switch_at_start_or_during_read_refuses(self):
        memory = RomMemory(self.variant())
        with self.assertRaisesRegex(CompatibilityError, "开始前已切换"):
            load_profile(crc="00000000", memory=memory, root=self.root)
        old_crc = memory.crc
        memory.on_read = lambda: setattr(memory, "crc", "ffffffff")
        with self.assertRaisesRegex(CompatibilityError, "期间已切换"):
            load_profile(crc=old_crc, memory=memory, root=self.root)

    def test_runtime_rechecks_metadata_even_with_cached_crc(self):
        memory = RomMemory(self.variant())
        profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
        runtime_compatibility_check(memory, profile)
        memory.rom[0x40000] ^= 1
        runtime_compatibility_check(memory, profile, full=False)
        with self.assertRaisesRegex(ValueError, "元数据表"):
            runtime_compatibility_check(memory, profile)
        memory.crc = "ffffffff"
        with self.assertRaisesRegex(ValueError, "ROM已切换"):
            runtime_compatibility_check(memory, profile, full=False)

    def test_bounded_length_and_short_reads_refuse(self):
        with self.assertRaisesRegex(CompatibilityError, "长度"):
            load_profile(b"small file", root=self.root)
        memory = RomMemory(self.variant())
        memory.read = lambda address, size: b"\0" * (size - 1)
        with self.assertRaisesRegex(CompatibilityError, "读取不完整"):
            load_profile(crc=memory.crc, memory=memory, root=self.root)

    def test_manifest_invalid_address_is_rejected_before_reading_guard(self):
        for entry in self.manifest["releases"].values():
            entry["guards"][0]["address"] = ROM_BASE + ROM_SIZE
        self.write_manifest()
        memory = RomMemory(self.variant())
        with self.assertRaisesRegex(CompatibilityError, "契约地址"):
            load_profile(crc=memory.crc, memory=memory, root=self.root)
        self.assertFalse(memory.reads)

    def test_tampered_profile_numeric_metadata_refuses(self):
        filename = next(iter(self.releases.values()))[2]
        path = self.root / filename
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["moves"]["1"]["pp"] = 99
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(CompatibilityError, "基线身份"):
            load_profile(self.variant(), root=self.root)

    def test_ambiguous_baselines_are_rejected_with_diagnostic(self):
        baseline_sha = next(iter(self.releases))
        baseline_entry = self.manifest["releases"][baseline_sha]
        baseline_profile = json.loads((self.root / self.releases[baseline_sha][2]).read_text())
        for sha, (_, _, filename) in self.releases.items():
            profile = json.loads((self.root / filename).read_text())
            profile["signatures"] = baseline_profile["signatures"]
            (self.root / filename).write_text(json.dumps(profile), encoding="utf-8")
            entry = self.manifest["releases"][sha]
            entry["guards"] = baseline_entry["guards"]
            entry["profile_sha256"] = profile_digest(profile)
        self.write_manifest()
        with self.assertRaisesRegex(CompatibilityError, "不唯一") as raised:
            load_profile(self.variant(), root=self.root)
        self.assertEqual(len([c for c in raised.exception.compatibility_report["candidates"]
                              if c["result"] == "matched"]), 3)

    def test_known_profile_identity_and_shape_stay_unchanged(self):
        for sha, (_, crc, filename) in self.releases.items():
            profile = load_profile(crc=crc, root=self.root)
            self.assertEqual(profile, json.loads((self.root / filename).read_text()))
            self.assertEqual(rom_identity(profile), sha)
            self.assertNotIn("compatibility", profile)
            runtime_compatibility_check(None, profile)

    def test_packaged_contract_validation_requires_each_profile_without_rom(self):
        result = validate_contracts(self.root)
        self.assertEqual(set(result), set(self.releases))
        first = next(iter(self.releases))
        del self.manifest["releases"][first]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "缺少"):
            validate_contracts(self.root)


class PackagedFontContractTests(unittest.TestCase):
    def test_each_real_contract_covers_full_normal_and_small_font_slots(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["releases"]), 3)
        self.assertEqual(FONT_MAPPING_RANGE[1] - FONT_MAPPING_RANGE[0], 2 * 28 * 256 * 64)
        for entry in manifest["releases"].values():
            with self.subTest(version=entry["baseline_version"]):
                cursor = FONT_MAPPING_RANGE[0]
                for guard in sorted(entry["guards"], key=lambda g: g["address"]):
                    start, end = guard["address"], guard["address"] + guard["size"]
                    if start <= cursor < end:
                        cursor = end
                    if cursor >= FONT_MAPPING_RANGE[1]:
                        break
                self.assertGreaterEqual(cursor, FONT_MAPPING_RANGE[1])
                self.assertIn("字形映射", entry["scope"])


if __name__ == "__main__":
    unittest.main()
