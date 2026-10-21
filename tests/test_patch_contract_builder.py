"""Research-only layout hypotheses cannot become unchecked runtime identities."""

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rom_compatibility import validate_patch_contracts
from rom_versions import RELEASES
from tools import extract_profile
from tools.build_patch_contract import APPROVED_PATCHES, build


class PatchContractBuilderTests(unittest.TestCase):
    def test_layout_research_keeps_actual_identity_and_exact_extraction_strict(self):
        rom = bytes(0x02000000)
        sha = next(iter(RELEASES))
        with patch.object(extract_profile, "_extract", return_value={"fixture": True}) as observed:
            result = extract_profile.extract_for_layout(rom, {}, sha)
            self.assertEqual(result, {"fixture": True})
            self.assertEqual(observed.call_args.args[2], hashlib.sha256(rom).hexdigest())
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
        data = json.loads((root / "rom_patch_compatibility.json").read_text(encoding="utf-8"))
        validated = validate_patch_contracts(root)
        approved = next(iter(APPROVED_PATCHES.values()))
        entry = next(e for e in data["variants"] if e["id"] == approved["id"])
        self.assertIn(entry["id"], validated)
        self.assertEqual(entry["patch_sha256"], next(iter(APPROVED_PATCHES)))
        self.assertEqual(entry["baseline_sha256"], approved["baseline_sha256"])
        self.assertEqual(entry["payload_regions"], [{"address": a, "size": b - a}
                                                   for a, b in approved["payload_regions"]])
        self.assertEqual(entry["source_crc32"], "518482f7")
        self.assertEqual(entry["target_crc32"], "51553df6")
        self.assertNotIn(entry["target_sha256"], RELEASES)
        self.assertNotIn(entry["target_crc32"], [info[1] for info in RELEASES.values()])


if __name__ == "__main__":
    unittest.main()
