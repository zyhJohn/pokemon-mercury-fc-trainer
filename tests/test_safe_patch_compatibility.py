"""Safety contracts use full structural guards and keep actual identity separate."""

import copy
import hashlib
import json
import os
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rom_compatibility import (
    PATCH_MANIFEST_NAME,
    PATCH_SCHEMA,
    ROM_BASE,
    CompatibilityError,
    validate_patch_contracts,
)
from rom_versions import (
    RELEASES,
    load_profile,
    rom_identity,
    runtime_compatibility_check,
)
from tests import test_rom_compatibility as structural
from tools import verify_builtin_patch as verifier
from tools.build_patch_contract import APPROVED_PATCHES

SAFE_IDS = (
    "builtin-editor-safe-v10",
    "builtin-editor-safe-v12",
    "builtin-editor-safe-v133",
)
PAYLOAD_START, PAYLOAD_END = 0x08A7A000, 0x08A80000


def uncovered(start, end, guards):
    cursor = start
    for guard in sorted(guards, key=lambda g: g["address"]):
        left, right = guard["address"], guard["address"] + guard["size"]
        if right <= cursor:
            continue
        if left > cursor:
            return True
        cursor = right
        if cursor >= end:
            return False
    return cursor < end


class ShippedSafetyContractTests(unittest.TestCase):
    def test_three_entries_match_audited_source_patch_target_pins(self):
        root = Path(__file__).resolve().parents[1]
        variants = json.loads((root / PATCH_MANIFEST_NAME).read_text(encoding="utf-8"))[
            "variants"
        ]
        shipped = {entry["id"]: entry for entry in variants}
        validated = validate_patch_contracts(root)
        self.assertEqual(len(shipped), len(variants))
        self.assertIn("builtin-editor-v133", shipped)
        for patch_sha, approved in APPROVED_PATCHES.items():
            if approved["id"] not in SAFE_IDS:
                continue
            with self.subTest(patch=approved["id"]):
                entry = shipped[approved["id"]]
                self.assertIn(entry["id"], validated)
                self.assertEqual(entry["patch_sha256"], patch_sha)
                for key in (
                    "baseline_sha256",
                    "target_sha256",
                    "target_crc32",
                    "patch_crc32",
                    "name",
                ):
                    self.assertEqual(entry[key], approved[key])
                self.assertEqual(
                    entry["source_crc32"], RELEASES[entry["baseline_sha256"]][1]
                )
                self.assertNotIn(entry["target_sha256"], RELEASES)
                self.assertNotIn(
                    entry["target_crc32"], {info[1] for info in RELEASES.values()}
                )
        self.assertTrue(set(SAFE_IDS) <= shipped.keys())

    def test_every_baseline_guard_and_full_payload_including_safety_caves_remains_protected(
        self,
    ):
        root = Path(__file__).resolve().parents[1]
        baseline = json.loads(
            (root / "rom_compatibility_manifest.json").read_text(encoding="utf-8")
        )["releases"]
        variants = json.loads((root / PATCH_MANIFEST_NAME).read_text(encoding="utf-8"))[
            "variants"
        ]
        safe = {entry["id"]: entry for entry in variants if entry["id"] in SAFE_IDS}
        self.assertEqual(set(safe), set(SAFE_IDS))
        for entry in safe.values():
            with self.subTest(patch=entry["id"]):
                original = baseline[entry["baseline_sha256"]]
                geometry = {
                    (g["address"], g["size"], g["kind"]): g for g in entry["guards"]
                }
                for guard in original["guards"]:
                    self.assertIn(
                        (guard["address"], guard["size"], guard["kind"]), geometry
                    )
                self.assertEqual(
                    entry["payload_regions"],
                    [{"address": PAYLOAD_START, "size": PAYLOAD_END - PAYLOAD_START}],
                )
                self.assertFalse(uncovered(PAYLOAD_START, PAYLOAD_END, entry["guards"]))
                for address in (0x08A7FD00, 0x08A7FE00, PAYLOAD_END - 1):
                    self.assertFalse(uncovered(address, address + 1, entry["guards"]))
                exceptions = entry["metadata_exceptions"]
                self.assertEqual(len(exceptions), 1)
                changed = []
                for guard in original["guards"]:
                    key = (guard["address"], guard["size"], guard["kind"])
                    replacement = geometry[key]
                    if (
                        guard["kind"] == "metadata"
                        and guard["sha256"] != replacement["sha256"]
                    ):
                        changed.append((guard, replacement))
                self.assertEqual(len(changed), 1)
                before, after = changed[0]
                self.assertEqual(exceptions[0]["address"], before["address"])
                self.assertEqual(exceptions[0]["size"], before["size"])
                self.assertEqual(exceptions[0]["baseline_sha256"], before["sha256"])
                self.assertEqual(exceptions[0]["target_sha256"], after["sha256"])
                self.assertTrue(exceptions[0]["reason"].strip())


class SyntheticSafetyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        # Reuse construction only; no inherited/imported TestCase test methods.
        structural.StructuralCompatibilityTests.setUp(self)
        self.targets, self.entries = [], []
        for index, patch_id in enumerate(SAFE_IDS):
            sha = list(self.releases)[index]
            target = bytearray(self.roms[index])
            target[0x500:0x504] = b"hook"
            target[PAYLOAD_START - ROM_BASE : PAYLOAD_END - ROM_BASE] = bytes(
                [0x80 + index]
            ) * (PAYLOAD_END - PAYLOAD_START)
            target[0x40003] ^= 0x80
            original = self.manifest["releases"][sha]
            entry = copy.deepcopy(original)
            for guard in entry["guards"]:
                start = guard["address"] - ROM_BASE
                guard["sha256"] = hashlib.sha256(
                    target[start : start + guard["size"]]
                ).hexdigest()
            entry["guards"].append(
                {
                    "address": PAYLOAD_START,
                    "size": PAYLOAD_END - PAYLOAD_START,
                    "kind": "engine",
                    "sha256": hashlib.sha256(
                        target[PAYLOAD_START - ROM_BASE : PAYLOAD_END - ROM_BASE]
                    ).hexdigest(),
                }
            )
            old_metadata, new_metadata = original["guards"][-1], entry["guards"][-2]
            entry.update(
                {
                    "id": patch_id,
                    "name": "synthetic safety fixture",
                    "patch_sha256": hashlib.sha256(patch_id.encode()).hexdigest(),
                    "source_crc32": self.releases[sha][1],
                    "target_crc32": f"{zlib.crc32(target):08x}",
                    "target_sha256": hashlib.sha256(target).hexdigest(),
                    "patch_crc32": "1234abcd",
                    "profile_values_unchanged": True,
                    "payload_regions": [
                        {"address": PAYLOAD_START, "size": PAYLOAD_END - PAYLOAD_START}
                    ],
                    "metadata_exceptions": [
                        {
                            "address": old_metadata["address"],
                            "size": old_metadata["size"],
                            "baseline_sha256": old_metadata["sha256"],
                            "target_sha256": new_metadata["sha256"],
                            "reason": "synthetic callback changed; profile values remain equal",
                        }
                    ],
                }
            )
            self.targets.append(target)
            self.entries.append(entry)
        self.write_patches()

    def write_manifest(self):
        structural.StructuralCompatibilityTests.write_manifest(self)

    def write_patches(self, entries=None):
        (self.root / PATCH_MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema": PATCH_SCHEMA,
                    "variants": self.entries if entries is None else entries,
                }
            ),
            encoding="utf-8",
        )

    def test_three_ids_recognize_offline_online_with_distinct_actual_identity(self):
        for entry, target in zip(self.entries, self.targets):
            with self.subTest(patch=entry["id"]):
                local = load_profile(target, root=self.root)
                memory = structural.LargeRomMemory(target)
                online = load_profile(crc=memory.crc, memory=memory, root=self.root)
                for profile in (local, online):
                    self.assertEqual(profile["layout_sha256"], entry["baseline_sha256"])
                    self.assertEqual(profile["rom_sha256"], entry["baseline_sha256"])
                    self.assertEqual(profile["compatibility"]["patch_id"], entry["id"])
                    self.assertEqual(
                        profile["compatibility"]["target_sha256"],
                        entry["target_sha256"],
                    )
                    self.assertEqual(profile["moves"]["1"]["pp"], 35)
                self.assertEqual(
                    rom_identity(local), "sha256:" + entry["target_sha256"]
                )
                self.assertEqual(rom_identity(online), "crc32:" + entry["target_crc32"])
                self.assertNotEqual(rom_identity(local), entry["baseline_sha256"])
                self.assertNotIn("actual_rom_sha256", online)

    def test_cached_target_crc_cannot_hide_safety_cave_or_engine_metadata_changes(self):
        for entry, target in zip(self.entries, self.targets):
            memory = structural.LargeRomMemory(target)
            profile = load_profile(crc=memory.crc, memory=memory, root=self.root)
            for offset in (0xA7A100, 0xA7FD00, 0xA7FE00, 0xA7FFFF, 0x500, 0x40000):
                with self.subTest(patch=entry["id"], offset=offset):
                    memory.rom[offset] ^= 1
                    try:
                        self.assertEqual(memory.crc, entry["target_crc32"])
                        runtime_compatibility_check(memory, profile, full=False)
                        with self.assertRaisesRegex(ValueError, "指纹不匹配"):
                            runtime_compatibility_check(memory, profile, full=True)
                    finally:
                        memory.rom[offset] ^= 1
                    runtime_compatibility_check(memory, profile, full=True)

    def test_offline_online_refuse_corrupt_safety_payload_even_with_pinned_target_crc(
        self,
    ):
        for entry, target in zip(self.entries, self.targets):
            for offset in (0xA7FD00, 0xA7FE00):
                with self.subTest(patch=entry["id"], offset=offset):
                    changed = bytearray(target)
                    changed[offset] ^= 1
                    memory = structural.LargeRomMemory(changed)
                    memory.crc = entry["target_crc32"]
                    with self.assertRaises(CompatibilityError):
                        load_profile(changed, root=self.root)
                    with self.assertRaises(CompatibilityError):
                        load_profile(crc=memory.crc, memory=memory, root=self.root)

    def test_one_byte_guard_holes_at_each_safety_cave_are_invalid_contracts(self):
        for index, original in enumerate(self.entries):
            for address in (0x08A7FD00, 0x08A7FE00):
                with self.subTest(patch=original["id"], address=address):
                    entries = copy.deepcopy(self.entries)
                    entry = entries[index]
                    payload = entry["guards"].pop()
                    entry["guards"].extend(
                        (
                            dict(payload, size=address - PAYLOAD_START),
                            dict(
                                payload,
                                address=address + 1,
                                size=PAYLOAD_END - address - 1,
                            ),
                        )
                    )
                    self.write_patches(entries)
                    with self.assertRaisesRegex(ValueError, "载荷区域.*完整覆盖"):
                        validate_patch_contracts(self.root)
        self.write_patches()


class VerificationCliSafetyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source, self.ups, self.template = (
            self.root / name for name in ("source.json", "patch.json", "template.json")
        )
        for path in (self.source, self.ups, self.template):
            path.write_bytes(path.stem.encode("ascii"))

    def test_report_input_aliases_and_hardlinks_fail_before_building(self):
        linked = self.root / "linked.json"
        os.link(self.template, linked)
        for report in (self.source, self.ups, self.template, linked):
            with (
                self.subTest(report=report),
                patch(
                    "sys.argv",
                    [
                        "verify",
                        str(self.source),
                        str(self.ups),
                        "--safe-template",
                        str(self.template),
                        "--report",
                        str(report),
                    ],
                ),
                patch.object(verifier, "build_safe") as build,
            ):
                with self.assertRaisesRegex(ValueError, "覆盖"):
                    verifier.main()
                build.assert_not_called()
        self.assertEqual(self.template.read_bytes(), b"template")

    def test_safe_cli_uses_safe_builder_target_and_atomic_json_report(self):
        output = self.root / "report.json"
        entry = {"id": SAFE_IDS[0], "target_crc32": "12345678"}
        report = {"baseline_version": "1.0"}
        profile = {"compatibility": {"patch_id": SAFE_IDS[0]}}
        with (
            patch(
                "sys.argv",
                [
                    "verify",
                    str(self.source),
                    str(self.ups),
                    "--safe-template",
                    str(self.template),
                    "--report",
                    str(output),
                ],
            ),
            patch.object(verifier, "build_safe", return_value=(entry, report)) as build,
            patch.object(
                verifier,
                "build_safe_candidate",
                return_value=SimpleNamespace(image=b"target"),
            ) as compose,
            patch.object(verifier, "load_profile", return_value=profile),
            patch.object(
                Path, "read_text", return_value=json.dumps({"variants": [entry]})
            ),
            patch("builtins.print"),
        ):
            verifier.main()
        self.assertEqual(build.call_args.args, (b"source", b"template", b"patch"))
        self.assertEqual(compose.call_args.args, (b"source", b"template", b"patch"))
        written = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(written["shipped_contract_reproduced"])
        self.assertTrue(written["offline_online_recognition"])
        self.assertEqual(self.source.read_bytes(), b"source")


if __name__ == "__main__":
    unittest.main()
