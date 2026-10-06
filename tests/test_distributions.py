"""Offline checks for source identity and selectable distribution templates."""

import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from box_data import BoxPokemon
from distribution_catalog import load_distributions, usable_rows
from pokemon_creation import create_box_pokemon

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_distributions()
        cls.profiles = [
            json.loads((ROOT / filename).read_text(encoding="utf-8"))
            for filename in ("rom_profile.json", "rom_profile_v12.json")
        ]

    def test_native_home_keys_match_complete_records(self):
        rows = [r for r in self.catalog["rows"] if r["source_group"] == "水银 HOME"]
        self.assertEqual(len(rows), 4)
        for row in rows:
            key = row["source_fields"]["manual_key"]
            self.assertTrue(key.startswith("PMH1."))
            encoded = key.split(".", 1)[1]
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            self.assertEqual(raw.hex(), row["template"]["native_pc_hex"])
            self.assertEqual(len(raw), 58)
            for profile in self.profiles:
                self.assertEqual(BoxPokemon(raw).describe(profile)["errors"], [])

    def test_external_adaptations_rebuild_identically_in_both_profiles(self):
        rows = [r for r in self.catalog["rows"] if r["source_group"] in ("火红叶绿适配", "其他版本适配")]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["compatibility"], "verified")
            self.assertEqual(row["template"]["native_format"], "adapted_gen3_pk3_to_mercury_fc_box58")
            self.assertEqual(len(row["source_fields"]["source_sha256"]), 64)
            expected = bytes.fromhex(row["template"]["native_pc_hex"])
            self.assertEqual(hashlib.sha256(expected).hexdigest(), row["template"]["sha256"])
            fields = {k: v for k, v in row["template"].items() if k not in ("native_format", "native_pc_hex", "sha256")}
            for profile in self.profiles:
                self.assertEqual(create_box_pokemon(profile, **fields).raw, expected)
                self.assertEqual(BoxPokemon(expected).describe(profile)["errors"], [])

    def test_pending_source_is_not_selectable(self):
        pending = [r for r in self.catalog["rows"] if r["compatibility"] == "pending"]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["template"])
        for profile in self.profiles:
            choices = usable_rows(self.catalog, profile)
            self.assertEqual(len(choices), 6)
            self.assertNotIn(pending[0], choices)

    def test_reject_modified_template_hash(self):
        bad = copy.deepcopy(self.catalog)
        bad["rows"][0]["template"]["native_pc_hex"] = "00" * 58
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "distributions.json"
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "哈希错误"):
                load_distributions(path)


if __name__ == "__main__":
    unittest.main()
