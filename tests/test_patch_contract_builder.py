"""Research-only layout hypotheses cannot become unchecked runtime identities."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rom_compatibility import validate_patch_contracts
from rom_versions import RELEASES
from tools import build_patch_contract as builder
from tools import extract_profile
from tools.build_patch_contract import APPROVED_PATCHES, build
from ups_patch import encode_ups, parse_ups


class PatchContractBuilderTests(unittest.TestCase):
    def test_layout_research_keeps_actual_identity_and_exact_extraction_strict(self):
        rom = bytes(0x02000000)
        sha = next(iter(RELEASES))
        with patch.object(
            extract_profile, "_extract", return_value={"fixture": True}
        ) as observed:
            result = extract_profile.extract_for_layout(rom, {}, sha)
            self.assertEqual(result, {"fixture": True})
            self.assertEqual(
                observed.call_args.args[2], hashlib.sha256(rom).hexdigest()
            )
            self.assertEqual(observed.call_args.args[3], RELEASES[sha][0])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            extract_profile.extract(rom, {})

    def test_research_requires_known_layout_and_exact_size(self):
        for rom, sha in ((b"", next(iter(RELEASES))), (bytes(0x02000000), "0" * 64)):
            with self.subTest(size=len(rom)), self.assertRaises(ValueError):
                extract_profile.extract_for_layout(rom, {}, sha)

    def test_unreviewed_ups_is_refused_before_any_source_or_manifest_access(self):
        with self.assertRaisesRegex(ValueError, "UPS"):
            build(b"not a ROM", b"unreviewed patch", root=Path("does-not-exist"))

    def test_shipped_patch_contract_matches_reviewed_payload_and_provenance(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads(
            (root / "rom_patch_compatibility.json").read_text(encoding="utf-8")
        )
        validated = validate_patch_contracts(root)
        approved = next(iter(APPROVED_PATCHES.values()))
        entry = next(e for e in data["variants"] if e["id"] == approved["id"])
        self.assertIn(entry["id"], validated)
        self.assertEqual(entry["patch_sha256"], next(iter(APPROVED_PATCHES)))
        self.assertEqual(entry["baseline_sha256"], approved["baseline_sha256"])
        self.assertEqual(
            entry["payload_regions"],
            [{"address": a, "size": b - a} for a, b in approved["payload_regions"]],
        )
        self.assertEqual(entry["source_crc32"], "518482f7")
        self.assertEqual(entry["target_crc32"], "51553df6")
        self.assertNotIn(entry["target_sha256"], RELEASES)
        self.assertNotIn(entry["target_crc32"], [info[1] for info in RELEASES.values()])

    def test_three_safety_definitions_pin_full_identities_and_guard_allocation(self):
        expected = {
            "v10": (
                "8310111595206cc4b1ab011c6199cac0c134387658977d5af062e16374c003cc",
                "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821",
                "093a110d43d9adbd1aa4a8b2fcdc54dff0eebce8a343b8f54354dbdb5d540de3",
                "85c03f81",
                "b96b411a",
            ),
            "v12": (
                "0185857d265aa00d3465a7d29fd20d078671a17d66e3b43d49c423ff6baeb5ca",
                "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896",
                "2803552d5e5bb37855080bdef5116fc75f8f8064ddfd7f6b6024dd5a338739d5",
                "0f613575",
                "fb95bb58",
            ),
            "v133": (
                "f91014b7347e123dff550c03ab36eb4ace632d2b1cf581197aa7b3622554a68d",
                "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4",
                "440441659ee4273a3e0a7af87207a160ee5406deceef2359c991659316bcafb5",
                "19b04315",
                "95a9f1b4",
            ),
        }
        self.assertEqual(len(APPROVED_PATCHES), 4)
        for suffix, (
            patch_sha,
            source_sha,
            target_sha,
            target_crc,
            footer_crc,
        ) in expected.items():
            with self.subTest(version=suffix):
                approved = APPROVED_PATCHES[patch_sha]
                self.assertEqual(approved["id"], f"builtin-editor-safe-{suffix}")
                self.assertEqual(approved["name"], "内置修改器安全移植（开发核验）")
                self.assertEqual(approved["baseline_sha256"], source_sha)
                self.assertEqual(approved["target_sha256"], target_sha)
                self.assertEqual(approved["target_crc32"], target_crc)
                self.assertEqual(approved["patch_crc32"], footer_crc)
                self.assertEqual(approved["transaction_feature_count"], 90)
                self.assertEqual(
                    approved["payload_regions"], [(0x08A7A000, 0x08A80000)]
                )
                for address in (0x08A7FD00, 0x08A7FE00, 0x08A7FFFF):
                    self.assertFalse(
                        builder._subtract(
                            address,
                            address + 1,
                            [
                                {"address": a, "size": b - a}
                                for a, b in approved["payload_regions"]
                            ],
                        )
                    )

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = bytes(128)
        target = bytearray(source)
        target[16], target[64] = 1, 2
        target = bytes(target)
        ups = encode_ups(source, target)
        parsed = parse_ups(ups)
        sha = hashlib.sha256(source).hexdigest()
        approved = {
            "id": "builtin-editor-safe-fixture",
            "name": "fixture",
            "baseline_sha256": sha,
            "target_sha256": hashlib.sha256(target).hexdigest(),
            "target_crc32": parsed.target_crc32,
            "patch_crc32": parsed.patch_crc32,
            "payload_regions": [(builder.ROM_BASE + 64, builder.ROM_BASE + 128)],
            "transaction_feature_count": 90,
            "metadata_reason": "fixture pointer",
        }
        guards = [
            {
                "address": builder.ROM_BASE + offset,
                "size": size,
                "kind": kind,
                "sha256": hashlib.sha256(source[offset : offset + size]).hexdigest(),
            }
            for offset, size, kind in ((0, 8, "engine"), (16, 4, "metadata"))
        ]
        original = {"baseline_sha256": sha, "guards": guards, "allowed_changes": []}
        profile = {
            "signatures": [{"address": builder.ROM_BASE + 8, "hex": "00"}] * 88,
            "storage": {"signatures": []},
            "economy": {"signatures": []},
            "time": {"signatures": []},
            "rom_sha256": sha,
            "rom_crc32": "source",
        }
        extracted = {
            **profile,
            "rom_sha256": hashlib.sha256(target).hexdigest(),
            "rom_crc32": "target",
        }
        wild = {
            "guards": [],
            "generator": builder.ROM_BASE + 8,
            "original_generator": "00",
            "stub_address": builder.ROM_BASE + 8,
            "original_stub": "00",
        }
        (root / "catalog.json").write_text("{}", encoding="utf-8")
        (root / "rom_compatibility_manifest.json").write_text(
            json.dumps({"releases": {sha: original}}), encoding="utf-8"
        )
        for name, replacement in (
            ("APPROVED_PATCHES", {hashlib.sha256(ups).hexdigest(): approved}),
            ("release", lambda _: (sha, ("fixture", "crc", "profile"))),
            ("load_profile", lambda *a, **kw: profile),
            ("build_manifest_entry", lambda *a: original),
            ("extract_for_layout", lambda *a, **kw: extracted),
            ("unown_rom_features", lambda _: ()),
            ("wild_layout", lambda _: wild),
        ):
            patcher = patch.object(builder, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return SimpleNamespace(
            root=root,
            source=source,
            target=target,
            ups=ups,
            approved=approved,
            original=original,
            profile=profile,
            extracted=extracted,
        )

    def test_build_retains_baseline_guard_geometry_and_covers_entire_payload(self):
        fixture = self.fixture()
        entry, report = build(fixture.source, fixture.ups, root=fixture.root)
        geometry = {(g["address"], g["size"], g["kind"]) for g in entry["guards"]}
        for guard in fixture.original["guards"]:
            self.assertIn((guard["address"], guard["size"], guard["kind"]), geometry)
        self.assertFalse(
            builder._subtract(
                builder.ROM_BASE + 64, builder.ROM_BASE + 128, entry["guards"]
            )
        )
        self.assertEqual(report["transaction_features_unchanged"], 90)
        self.assertTrue(report["reverse_roundtrip"])
        self.assertTrue(entry["profile_values_unchanged"])
        self.assertEqual(len(entry["metadata_exceptions"]), 1)
        self.assertEqual(
            fixture.original["guards"][1]["sha256"],
            hashlib.sha256(bytes(4)).hexdigest(),
        )

    def test_build_rejects_target_footer_and_feature_count_drift(self):
        for key, value in (
            ("target_sha256", "0" * 64),
            ("target_crc32", "00000000"),
            ("patch_crc32", "00000000"),
            ("transaction_feature_count", 89),
        ):
            with self.subTest(pin=key):
                fixture = self.fixture()
                fixture.approved[key] = value
                with self.assertRaises(ValueError):
                    build(fixture.source, fixture.ups, root=fixture.root)

    def test_profile_actual_identity_may_differ_but_other_values_must_match(self):
        fixture = self.fixture()
        fixture.extracted["unexpected_field"] = 1
        with self.assertRaisesRegex(ValueError, "提取配置"):
            build(fixture.source, fixture.ups, root=fixture.root)

    def test_wrong_source_identity_is_refused_before_application(self):
        fixture = self.fixture()
        fixture.approved["baseline_sha256"] = "0" * 64
        with (
            patch.object(builder, "apply_ups") as apply,
            self.assertRaisesRegex(ValueError, "原版布局"),
        ):
            build(fixture.source, fixture.ups, root=fixture.root)
        apply.assert_not_called()

    def test_any_preserved_transaction_feature_change_is_refused(self):
        fixture = self.fixture()
        fixture.profile["signatures"][0]["address"] = builder.ROM_BASE + 64
        with self.assertRaisesRegex(ValueError, "代码守卫冲突"):
            build(fixture.source, fixture.ups, root=fixture.root)

    def test_reverse_roundtrip_is_required_even_after_identity_checks(self):
        fixture = self.fixture()
        with (
            patch.object(
                builder, "apply_ups", side_effect=(fixture.target, b"wrong")
            ) as apply,
            self.assertRaisesRegex(ValueError, "反向恢复"),
        ):
            build(fixture.source, fixture.ups, root=fixture.root)
        self.assertEqual(apply.call_args.kwargs, {"reverse": True})

    def test_short_preimage_and_guard_read_are_refused(self):
        for address, size in ((builder.ROM_BASE - 1, 1), (builder.ROM_BASE + 128, 1)):
            with (
                self.subTest(address=address),
                self.assertRaisesRegex(ValueError, "原像不足"),
            ):
                builder._bytes(bytes(128), address, size)

    def test_build_safe_composes_only_in_memory_then_uses_strict_build(self):
        fixture = self.fixture()
        with patch.object(
            builder,
            "build_safe_candidate",
            return_value=SimpleNamespace(ups=fixture.ups),
        ) as compose:
            entry, _ = builder.build_safe(
                fixture.source, b"template", b"original UPS", root=fixture.root
            )
        compose.assert_called_once_with(fixture.source, b"template", b"original UPS")
        self.assertEqual(entry["id"], "builtin-editor-safe-fixture")
        with (
            patch.object(
                builder,
                "build_safe_candidate",
                return_value=SimpleNamespace(ups=b"unknown"),
            ),
            self.assertRaisesRegex(ValueError, "UPS"),
        ):
            builder.build_safe(
                fixture.source, b"template", b"original UPS", root=fixture.root
            )


class PatchContractCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.ups = self.root / "source.json", self.root / "patch.json"
        self.source.write_text("source untouched", encoding="utf-8")
        self.ups.write_text("patch untouched", encoding="utf-8")
        self.entry = {"id": "fixture"}
        self.report = {
            "baseline_version": "fixture",
            "target_crc32": "12345678",
            "guard_count": 1,
            "checked_bytes": 5,
        }

    def invoke(self, *args):
        with (
            patch(
                "sys.argv",
                ["builder", str(self.source), str(self.ups), *map(str, args)],
            ),
            patch.object(builder, "build", return_value=(self.entry, self.report)),
            patch("builtins.print"),
        ):
            builder.main()

    def test_report_only_is_allowed_and_creates_no_manifest(self):
        report = self.root / "report.json"
        self.invoke("--report", report)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), self.report)
        self.assertEqual(
            {p.name for p in self.root.iterdir()},
            {"source.json", "patch.json", "report.json"},
        )

    def test_input_alias_second_destination_prevents_any_first_output_write(self):
        output = self.root / "new-output.json"
        with self.assertRaisesRegex(ValueError, "覆盖"):
            self.invoke("--output", output, "--report", self.source)
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_text(encoding="utf-8"), "source untouched")

    def test_output_report_alias_and_hardlinks_rejected_before_either_mutates(self):
        output = self.root / "output.json"
        output.write_text(
            '{"schema":"mercury-patched-layout-v1","variants":[]}', encoding="utf-8"
        )
        initial = output.read_bytes()
        linked = self.root / "linked.json"
        os.link(output, linked)
        for report in (output, linked):
            with (
                self.subTest(report=report),
                self.assertRaisesRegex(ValueError, "覆盖"),
            ):
                self.invoke("--output", output, "--report", report)
            self.assertEqual(output.read_bytes(), initial)

    def test_hardlink_to_input_and_invalid_second_suffix_leave_output_untouched(self):
        linked = self.root / "linked.json"
        os.link(self.ups, linked)
        output = self.root / "new-output.json"
        for report in (linked, self.root / "report.txt"):
            with self.subTest(report=report), self.assertRaises(ValueError):
                self.invoke("--output", output, "--report", report)
            self.assertFalse(output.exists())
        self.assertEqual(self.ups.read_text(encoding="utf-8"), "patch untouched")

    def test_manifest_merge_uses_json_writer_and_keeps_other_variants(self):
        output, report = self.root / "output.json", self.root / "report.json"
        output.write_text(
            json.dumps(
                {
                    "schema": builder.PATCH_SCHEMA,
                    "variants": [{"id": "other"}, {"id": "fixture", "old": True}],
                }
            ),
            encoding="utf-8",
        )
        self.invoke("--output", output, "--report", report)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["variants"],
            [{"id": "other"}, self.entry],
        )
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), self.report)

    def test_safe_template_is_also_protected_input(self):
        template = self.root / "template.json"
        template.write_text("template untouched", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "覆盖"):
            self.invoke("--safe-template", template, "--report", template)
        self.assertEqual(template.read_text(encoding="utf-8"), "template untouched")


if __name__ == "__main__":
    unittest.main()
