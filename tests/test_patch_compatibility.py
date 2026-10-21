"""Synthetic patch contracts; no private ROM or patch bytes are required."""

import copy
import hashlib
import json
import unittest
import zlib

from rom_compatibility import (
    CompatibilityError, PATCH_MANIFEST_NAME, PATCH_SCHEMA, ROM_BASE,
    validate_contracts, validate_patch_contracts,
)
from rom_versions import load_profile, rom_identity, runtime_compatibility_check
from tests import test_rom_compatibility as structural


class PatchedCompatibilityTests(unittest.TestCase):
    def setUp(self):
        # Reuse only fixture construction, without importing its TestCase into
        # this module's discovery namespace or inheriting its test methods.
        structural.StructuralCompatibilityTests.setUp(self)
        self.baseline_sha = list(self.releases)[2]
        self.patched = bytearray(self.roms[2])
        self.patched[0x500:0x504] = b"hook"
        self.patched[0x60000:0x60080] = b"payload!" * 16
        self.entry = copy.deepcopy(self.manifest["releases"][self.baseline_sha])
        for guard in self.entry["guards"]:
            start = guard["address"] - ROM_BASE
            guard["sha256"] = hashlib.sha256(self.patched[start:start + guard["size"]]).hexdigest()
        self.entry["guards"].append({
            "address": ROM_BASE + 0x60000, "size": 128, "kind": "engine",
            "sha256": hashlib.sha256(self.patched[0x60000:0x60080]).hexdigest(),
        })
        self.entry.update({
            "id": "builtin-editor-v133", "name": "内置修改器补丁",
            "patch_sha256": hashlib.sha256(b"synthetic patch identity").hexdigest(),
            "source_crc32": self.releases[self.baseline_sha][1],
            "target_crc32": f"{zlib.crc32(self.patched) & 0xFFFFFFFF:08x}",
            "target_sha256": hashlib.sha256(self.patched).hexdigest(),
            "patch_crc32": "1234abcd",
            "profile_values_unchanged": True,
            "payload_regions": [{"address": ROM_BASE + 0x60000, "size": 128}],
        })
        self.patch_manifest = {"schema": PATCH_SCHEMA, "variants": [self.entry]}
        self.write_patches()

    def write_manifest(self):
        structural.StructuralCompatibilityTests.write_manifest(self)

    def write_patches(self):
        (self.root / PATCH_MANIFEST_NAME).write_text(json.dumps(self.patch_manifest), encoding="utf-8")

    def modified(self):
        rom = bytearray(self.patched)
        rom[0xA0:0xAC] = b"MERCURY V1.1"
        rom[0xBC:0xBE] = b"\x99\x88"
        rom[0x90000:0x90004] = b"art!"
        return rom

    def assert_patch_identity(self, profile, actual_identity):
        self.assertEqual(profile["rom_sha256"], self.baseline_sha)
        self.assertEqual(profile["layout_sha256"], self.baseline_sha)
        self.assertEqual(rom_identity(profile), actual_identity)
        compat = profile["compatibility"]
        self.assertIn(self.entry["name"], compat["name"])
        self.assertEqual(compat["schema"], PATCH_SCHEMA)
        self.assertEqual(compat["patch_id"], self.entry["id"])
        self.assertEqual(compat["patch_sha256"], self.entry["patch_sha256"])
        self.assertEqual(compat["target_sha256"], self.entry["target_sha256"])
        self.assertEqual(profile["_compatibility_guards"][-1][0], ROM_BASE + 0x60000)
        self.assertEqual(profile["moves"]["1"]["pp"], 35)

    def test_offline_patch_and_allowed_title_and_asset_changes(self):
        for rom in (self.patched, self.modified()):
            with self.subTest(changed=rom != self.patched):
                profile = load_profile(rom, root=self.root)
                sha = hashlib.sha256(rom).hexdigest()
                self.assert_patch_identity(profile, "sha256:" + sha)
                self.assertEqual(profile["actual_rom_sha256"], sha)
                self.assertEqual(profile["rom_crc32"], f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}")

    def test_online_patch_and_allowed_title_and_asset_changes(self):
        for rom in (self.patched, self.modified()):
            with self.subTest(changed=rom != self.patched):
                memory = structural.RomMemory(rom)
                profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
                self.assert_patch_identity(profile, "crc32:" + memory.crc)
                self.assertNotIn("actual_rom_sha256", profile)
                self.assertTrue(memory.reads)
                self.assertTrue(all(size <= 4096 for _, size in memory.reads))

    def test_target_crc_cannot_authorize_corrupt_or_missing_payload(self):
        for offset in (0x500, 0x60000):
            with self.subTest(offset=offset):
                rom = bytearray(self.patched)
                rom[offset] ^= 1
                memory = structural.RomMemory(rom)
                memory.crc = self.entry["target_crc32"]
                with self.assertRaises(CompatibilityError):
                    load_profile(crc=memory.crc, memory=memory, root=self.root)

    def test_extra_payload_code_and_metadata_changes_refuse(self):
        for offset, reason in ((0x6007F, "代码/关键映射"), (0x550, "代码/关键映射"),
                               (0x40000, "元数据表")):
            with self.subTest(offset=offset):
                rom = self.modified()
                rom[offset] ^= 1
                with self.assertRaisesRegex(CompatibilityError, reason):
                    load_profile(rom, root=self.root)

    def test_offline_and_online_mismatch_reports_hash_actual_reads_without_rom_bytes(self):
        offline = self.modified()
        offline[0x550] ^= 1
        memory = structural.RomMemory(offline)
        memory.rom[0x560] ^= 1
        with self.assertRaises(CompatibilityError) as local:
            load_profile(offline, root=self.root)
        with self.assertRaises(CompatibilityError) as online:
            load_profile(crc=memory.crc, memory=memory, root=self.root)
        guard = self.entry["guards"][2]
        mismatches = []
        for raised, actual_rom in ((local, offline), (online, memory.rom)):
            report = raised.exception.compatibility_report
            candidate = next(c for c in report["candidates"] if c.get("patch_id") == self.entry["id"])
            mismatch = candidate["mismatch"]
            start = guard["address"] - ROM_BASE
            self.assertEqual(mismatch, {
                "address": guard["address"], "size": guard["size"], "kind": "engine",
                "expected_sha256": guard["sha256"],
                "actual_sha256": hashlib.sha256(actual_rom[start:start + guard["size"]]).hexdigest(),
            })
            self.assertEqual(candidate["reason"],
                             f"代码/关键映射指纹不匹配：0x{guard['address']:08X} / {guard['size']}字节；未开放编辑")
            # JSON serialization rejects any accidental bytes/bytearray fields;
            # the exact mismatch field set above excludes raw or encoded ROM.
            encoded = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("payload!", encoded)
            self.assertNotIn(bytes(actual_rom[start:start + 32]).hex(), encoded)
            mismatches.append(mismatch)
        self.assertNotEqual(mismatches[0]["actual_sha256"], mismatches[1]["actual_sha256"])

    def test_runtime_full_rechecks_patched_engine_and_payload_with_cached_crc(self):
        for offset in (0x500, 0x550, 0x6007F, 0x40000):
            with self.subTest(offset=offset):
                memory = structural.RomMemory(self.patched)
                profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
                runtime_compatibility_check(memory, profile)
                memory.rom[offset] ^= 1
                runtime_compatibility_check(memory, profile, full=False)
                with self.assertRaisesRegex(ValueError, "指纹不匹配"):
                    runtime_compatibility_check(memory, profile)

    def test_plain_and_known_release_diagnostics_remain_unchanged(self):
        plain = structural.StructuralCompatibilityTests.variant(self, 2)
        profile = load_profile(plain, root=self.root)
        self.assertNotIn("patch_id", profile["compatibility"])
        self.assertNotIn("patch_id", profile["compatibility"]["checks"]["candidates"][2])
        known = load_profile(self.roms[2], root=self.root)
        self.assertNotIn("compatibility", known)
        self.assertEqual(rom_identity(known), self.baseline_sha)

    def test_ambiguous_patch_candidates_refuse(self):
        second = copy.deepcopy(self.entry)
        second["id"] = "second-verified-patch"
        self.patch_manifest["variants"].append(second)
        self.write_patches()
        with self.assertRaisesRegex(CompatibilityError, "不唯一") as raised:
            load_profile(self.patched, root=self.root)
        matched = [c for c in raised.exception.compatibility_report["candidates"]
                   if c["result"] == "matched"]
        self.assertEqual({c["patch_id"] for c in matched}, {self.entry["id"], second["id"]})

    def test_patch_summary_and_existing_contract_summary_interfaces(self):
        summary = validate_patch_contracts(self.root)
        self.assertEqual(summary[self.entry["id"]]["guard_count"], len(self.entry["guards"]))
        self.assertEqual(set(validate_contracts(self.root)), set(self.releases))

    def test_missing_patch_file_required_for_explicit_validation_only(self):
        (self.root / PATCH_MANIFEST_NAME).unlink()
        with self.assertRaises(FileNotFoundError):
            validate_patch_contracts(self.root)
        self.assertEqual(set(validate_contracts(self.root)), set(self.releases))
        plain = structural.StructuralCompatibilityTests.variant(self, 2)
        self.assertNotIn("patch_id", load_profile(plain, root=self.root)["compatibility"])

    def test_invalid_patch_file_is_not_silently_ignored(self):
        for payload in ("{", "[]", "null", '{"schema":"bad","variants":[]}'):
            with self.subTest(payload=payload):
                (self.root / PATCH_MANIFEST_NAME).write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)
                plain = structural.StructuralCompatibilityTests.variant(self, 2)
                with self.assertRaises(CompatibilityError):
                    load_profile(plain, root=self.root)

    def test_provenance_baseline_and_profile_mismatches_refuse_before_reads(self):
        for field, value in (("baseline_sha256", "f" * 64), ("baseline_version", "1.2"),
                             ("source_crc32", "ffffffff"), ("profile_sha256", "f" * 64),
                             ("patch_sha256", "bad"), ("target_sha256", "A" * 64),
                             ("target_crc32", "123"), ("patch_crc32", "gggggggg")):
            with self.subTest(field=field):
                entry = copy.deepcopy(self.entry)
                entry[field] = value
                self.patch_manifest["variants"] = [entry]
                self.write_patches()
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)
                memory = structural.RomMemory(self.patched)
                with self.assertRaises(CompatibilityError):
                    load_profile(crc=memory.crc, memory=memory, root=self.root)
                self.assertFalse(memory.reads)

    def test_changed_root_profile_and_missing_baseline_refuse(self):
        filename = self.releases[self.baseline_sha][2]
        path = self.root / filename
        original = path.read_text(encoding="utf-8")
        profile = json.loads(original)
        profile["moves"]["1"]["pp"] += 1
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "基线身份"):
            validate_patch_contracts(self.root)
        path.write_text(original, encoding="utf-8")
        del self.manifest["releases"][self.baseline_sha]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "缺少"):
            validate_patch_contracts(self.root)

    def test_baseline_guards_and_metadata_hashes_cannot_be_removed_or_changed(self):
        mutations = (
            lambda guards: guards.pop(0),
            lambda guards: guards[0].update(size=guards[0]["size"] - 1),
            lambda guards: guards[0].update(kind="metadata"),
            lambda guards: guards[-2].update(sha256="f" * 64),
            lambda guards: guards.append(copy.deepcopy(guards[0])),
            lambda guards: guards[-1].update(kind="metadata"),
            lambda guards: guards[-1].update(address=ROM_BASE + 0x40000),
            lambda guards: guards[-1].update(size=65537),
            lambda guards: guards[-1].update(address=ROM_BASE - 1),
            lambda guards: guards.append(None),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                entry = copy.deepcopy(self.entry)
                mutate(entry["guards"])
                self.patch_manifest["variants"] = [entry]
                self.write_patches()
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)

    def test_variant_ids_and_count_are_bounded(self):
        invalid_lists = ([self.entry, self.entry], [dict(self.entry, id="../patch")],
                         [None], [dict(self.entry, id=f"patch-{i}") for i in range(33)])
        for variants in invalid_lists:
            with self.subTest(count=len(variants)):
                self.patch_manifest["variants"] = variants
                self.write_patches()
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)

    def test_guard_read_budget_is_bounded(self):
        # Valid disjoint addresses still exceed the cumulative per-candidate
        # guard budget; validation must reject before any bridge ROM reads.
        entry = copy.deepcopy(self.entry)
        for index in range(200):
            entry["guards"].append({"address": ROM_BASE + 0x100000 + index * 65536,
                                    "size": 65536, "kind": "engine", "sha256": "0" * 64})
        self.patch_manifest["variants"] = [entry]
        self.write_patches()
        with self.assertRaisesRegex(ValueError, "累计读取"):
            validate_patch_contracts(self.root)

    def test_payload_regions_are_required_bounded_and_nonoverlapping(self):
        mutations = (
            lambda entry: entry.pop("payload_regions"),
            lambda entry: entry.update(payload_regions=[]),
            lambda entry: entry.update(payload_regions=[None]),
            lambda entry: entry.update(payload_regions=[{"address": ROM_BASE - 1, "size": 128}]),
            lambda entry: entry.update(payload_regions=[{"address": ROM_BASE, "size": 0}]),
            lambda entry: entry.update(payload_regions=[{"address": ROM_BASE, "size": True}]),
            lambda entry: entry["payload_regions"].append({"address": ROM_BASE + 0x60040, "size": 64}),
            lambda entry: entry.update(payload_regions=[{"address": ROM_BASE + i, "size": 1}
                                                       for i in range(33)]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                entry = copy.deepcopy(self.entry)
                mutate(entry)
                self.patch_manifest["variants"] = [entry]
                self.write_patches()
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)
                with self.assertRaises(CompatibilityError):
                    load_profile(self.patched, root=self.root)

    def test_payload_guard_cannot_be_removed_truncated_or_leave_a_gap(self):
        for mode in ("removed", "tail", "gap"):
            with self.subTest(mode=mode):
                entry = copy.deepcopy(self.entry)
                guard = entry["guards"].pop()
                if mode == "tail":
                    entry["guards"].append(dict(guard, size=127))
                elif mode == "gap":
                    entry["guards"].extend((dict(guard, size=63),
                                             dict(guard, address=guard["address"] + 64, size=64)))
                self.patch_manifest["variants"] = [entry]
                self.write_patches()
                with self.assertRaisesRegex(ValueError, "载荷区域.*完整覆盖"):
                    validate_patch_contracts(self.root)

    def test_payload_region_can_be_covered_by_contiguous_guards(self):
        guard = self.entry["guards"].pop()
        for offset in (0, 64):
            data = self.patched[0x60000 + offset:0x60040 + offset]
            self.entry["guards"].append(dict(guard, address=guard["address"] + offset,
                                               size=64, sha256=hashlib.sha256(data).hexdigest()))
        self.write_patches()
        validate_patch_contracts(self.root)
        self.assertEqual(load_profile(self.patched, root=self.root)["compatibility"]["patch_id"],
                         self.entry["id"])

    def audited_metadata_fixture(self):
        # Synthetic item callback bytes share a protected numeric-table guard.
        # The trusted contract records the full guard's old/new hashes after a
        # separate extraction audit; recognition still reads every target byte.
        self.patched[0x40003] ^= 0x80
        guard = self.entry["guards"][-2]
        before = guard["sha256"]
        guard["sha256"] = hashlib.sha256(self.patched[0x40000:0x40004]).hexdigest()
        self.entry["metadata_exceptions"] = [{
            "address": guard["address"], "size": guard["size"],
            "baseline_sha256": before, "target_sha256": guard["sha256"],
            "reason": "Synthetic callback pointer changed; extracted numeric profile values compared equal",
        }]
        self.entry["target_sha256"] = hashlib.sha256(self.patched).hexdigest()
        self.entry["target_crc32"] = f"{zlib.crc32(self.patched) & 0xFFFFFFFF:08x}"
        self.write_patches()

    def test_explicitly_audited_callback_guard_accepts_and_numeric_tampering_refuses(self):
        self.audited_metadata_fixture()
        validate_patch_contracts(self.root)
        profile = load_profile(self.patched, root=self.root)
        self.assertEqual(profile["compatibility"]["metadata_exceptions"],
                         self.entry["metadata_exceptions"])
        memory = structural.RomMemory(self.patched)
        profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
        runtime_compatibility_check(memory, profile)
        memory.rom[0x40000] ^= 1
        with self.assertRaisesRegex(ValueError, "元数据表"):
            runtime_compatibility_check(memory, profile)
        with self.assertRaisesRegex(CompatibilityError, "元数据表"):
            load_profile(memory.rom, root=self.root)

    def test_metadata_exception_requires_exact_old_new_hashes_geometry_and_reason(self):
        self.audited_metadata_fixture()
        original = copy.deepcopy(self.entry)
        mutations = (
            lambda entry: entry.pop("profile_values_unchanged"),
            lambda entry: entry.update(profile_values_unchanged=1),
            lambda entry: entry.update(metadata_exceptions=[]),
            lambda entry: entry["metadata_exceptions"][0].update(address=ROM_BASE + 0x40001),
            lambda entry: entry["metadata_exceptions"][0].update(size=3),
            lambda entry: entry["metadata_exceptions"][0].update(baseline_sha256="0" * 64),
            lambda entry: entry["metadata_exceptions"][0].update(target_sha256="0" * 64),
            lambda entry: entry["metadata_exceptions"][0].update(reason=" "),
            lambda entry: entry["metadata_exceptions"].append(copy.deepcopy(entry["metadata_exceptions"][0])),
            lambda entry: entry.update(metadata_exceptions=[None]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                entry = copy.deepcopy(original)
                mutate(entry)
                self.patch_manifest["variants"] = [entry]
                self.write_patches()
                with self.assertRaises(ValueError):
                    validate_patch_contracts(self.root)
                with self.assertRaises(CompatibilityError):
                    load_profile(self.patched, root=self.root)

    def test_metadata_exception_for_unchanged_guard_is_rejected(self):
        guard = self.entry["guards"][-2]
        self.entry["metadata_exceptions"] = [{
            "address": guard["address"], "size": guard["size"],
            "baseline_sha256": guard["sha256"], "target_sha256": guard["sha256"],
            "reason": "No actual change to justify this exception",
        }]
        self.write_patches()
        with self.assertRaisesRegex(ValueError, "实际变更"):
            validate_patch_contracts(self.root)

    def test_nonbytes_and_short_bridge_reads_reject_with_compatibility_error(self):
        for bad_read in (lambda size: None, lambda size: b"\0" * (size - 1),
                         lambda size: "\0" * size):
            with self.subTest(reader=bad_read):
                memory = structural.LargeRomMemory(self.patched)
                original = memory.read_rom
                memory.read_rom = lambda address, size: (bad_read(size) if size > 4
                                                          else original(address, size))
                with self.assertRaisesRegex(CompatibilityError, "读取不完整"):
                    load_profile(crc=memory.crc, memory=memory, root=self.root)

    def test_actual_identity_changes_while_layout_and_patch_provenance_stay_fixed(self):
        a = load_profile(self.patched, root=self.root)
        b = load_profile(self.modified(), root=self.root)
        self.assertEqual(a["layout_sha256"], b["layout_sha256"])
        self.assertEqual(a["compatibility"]["patch_sha256"], b["compatibility"]["patch_sha256"])
        self.assertNotEqual(rom_identity(a), rom_identity(b))
        self.assertNotEqual(a["rom_crc32"], b["rom_crc32"])


if __name__ == "__main__":
    unittest.main()
