import copy
import json
import struct
import unittest

from pokemon_integrity import inspect_record, require_editable

PROFILE = {"species": {"25": {}}, "moves": {"33": {"pp": 35}}, "items": {"1": {}}}


def sample(kind="party", egg=False):
    raw = bytearray(100 if kind == "party" else 58)
    raw[19] = 2 | (4 if egg else 0)
    struct.pack_into("<H", raw, 32 if kind == "party" else 28, 25)
    struct.pack_into("<I", raw, 36 if kind == "party" else 32, 125)
    if kind == "party":
        struct.pack_into("<H", raw, 44, 33)
        raw[84] = 5
    else:
        raw[39:44] = (33).to_bytes(5, "little")
    struct.pack_into("<I", raw, 72 if kind == "party" else 54, (1 << 30) if egg else 0)
    return raw


class IntegrityTests(unittest.TestCase):
    def test_normal_and_legitimate_zero_pid_otid(self):
        for kind in ("party", "box"):
            with self.subTest(kind=kind):
                report = inspect_record(sample(kind), PROFILE, kind)
                self.assertEqual(report["status"], "healthy")
                self.assertTrue(report["editable"])
                self.assertEqual(report["observed"]["pid"], 0)
                self.assertEqual(report["observed"]["otid"], 0)
                self.assertEqual(report["errors"], [])
                json.dumps(report)

    def test_bad_flag_is_distinct_from_normal_egg(self):
        for kind in ("party", "box"):
            raw = sample(kind, egg=True)
            self.assertEqual(inspect_record(raw, PROFILE, kind)["status"], "healthy")
            raw[19] |= 1
            report = inspect_record(raw, PROFILE, kind)
            self.assertEqual(report["status"], "bad_egg")
            self.assertTrue(report["bad_egg"])
            self.assertTrue(report["damaged"])
            self.assertFalse(report["editable"])
            with self.assertRaisesRegex(ValueError, "坏蛋"):
                require_editable(raw, PROFILE, kind)

    def test_egg_flag_disagreement(self):
        for kind in ("party", "box"):
            for mismatch in ("header", "iv"):
                raw = sample(kind, egg=mismatch == "iv")
                raw[19] ^= 4
                if mismatch == "iv":
                    raw[19] &= ~4
                report = inspect_record(raw, PROFILE, kind)
                self.assertEqual(report["status"], "damaged")
                self.assertFalse(report["bad_egg"])
                self.assertIn("蛋标志不一致", " ".join(report["errors"]))

    def test_empty_only_when_entirely_zero_and_not_active_party(self):
        self.assertEqual(inspect_record(bytes(58), PROFILE, "box")["status"], "empty")
        self.assertEqual(inspect_record(bytes(100), PROFILE, active=False)["status"], "empty")
        active = inspect_record(bytes(100), PROFILE)
        self.assertEqual(active["status"], "damaged")
        self.assertFalse(active["empty"])
        for kind in ("party", "box"):
            raw = bytearray(100 if kind == "party" else 58)
            raw[0] = 1
            report = inspect_record(raw, PROFILE, kind, active=False)
            self.assertEqual(report["status"], "damaged")
            self.assertFalse(report["empty"])
            self.assertIn("残留", " ".join(report["errors"]))

    def test_unknown_ids_are_retained_without_metadata_access(self):
        for kind in ("party", "box"):
            raw = sample(kind)
            struct.pack_into("<H", raw, 32 if kind == "party" else 28, 65535)
            struct.pack_into("<H", raw, 34 if kind == "party" else 30, 65535)
            if kind == "party":
                struct.pack_into("<H", raw, 44, 65535)
            else:
                raw[39:44] = (1023).to_bytes(5, "little")
            report = inspect_record(raw, PROFILE, kind)
            self.assertEqual(report["status"], "damaged")
            self.assertEqual(len(report["errors"]), 3)
            self.assertEqual(report["observed"]["species"], 65535)
            self.assertEqual(report["observed"]["held"], 65535)
            self.assertEqual(report["observed"]["moves"][0], 65535 if kind == "party" else 1023)

    def test_editor_policies_do_not_imply_corruption(self):
        for kind in ("party", "box"):
            raw = sample(kind)
            if kind == "party":
                raw[56:62] = bytes([255] * 6)
                struct.pack_into("<H", raw, 46, 33)  # Repeated move.
                raw[53:56] = bytes([35] * 3)  # Actual unpack can give empty moves PP35.
                raw[88:100] = bytes([255] * 12)  # Never call a stat formula.
            else:
                raw[44:50] = bytes([255] * 6)
                raw[39:44] = (33 | 33 << 10).to_bytes(5, "little")
            self.assertEqual(inspect_record(raw, PROFILE, kind)["status"], "healthy")

    def test_party_level_range_without_deriving_box_level(self):
        for level in (0, 101, 255):
            raw = sample()
            raw[84] = level
            report = inspect_record(raw, PROFILE)
            self.assertEqual(report["status"], "damaged")
            self.assertIn("队伍等级", " ".join(report["errors"]))
        for level in (1, 100):
            raw = sample()
            raw[84] = level
            self.assertEqual(inspect_record(raw, PROFILE)["status"], "healthy")
        raw = sample("box")
        struct.pack_into("<I", raw, 32, 0xFFFFFFFF)
        self.assertEqual(inspect_record(raw, PROFILE, "box")["status"], "healthy")

    def test_has_species_header_bit_is_not_engine_has_species(self):
        # All three audited ROMs implement field5 as species != 0 && !badflag.
        # Header bit1 is copied by PC unpack but not used by this getter.
        for kind in ("party", "box"):
            raw = sample(kind)
            raw[19] &= ~2
            self.assertEqual(inspect_record(raw, PROFILE, kind)["status"], "healthy")

    def test_no_checksum_or_input_profile_mutation(self):
        raw = sample()
        raw[28:32] = b"\xEF\xBE\xAD\xDE"
        before = bytes(raw)
        profile = copy.deepcopy(PROFILE)
        report = require_editable(raw, profile)
        self.assertEqual(report["checksum"], "not-used-in-mercury")
        self.assertEqual(report["observed"]["form_restore_species"], 0xBEEF)
        self.assertEqual(bytes(raw), before)
        self.assertEqual(profile, PROFILE)

    def test_wrong_length_and_kind_rejected(self):
        for kind, length in (("party", 100), ("box", 58)):
            for wrong in (0, length - 1, length + 1):
                with self.assertRaises(ValueError):
                    inspect_record(bytes(wrong), PROFILE, kind)
        with self.assertRaises(ValueError):
            inspect_record(bytes(100), PROFILE, "unknown")
        with self.assertRaises(TypeError):
            inspect_record("x" * 100, PROFILE)
        with self.assertRaisesRegex(ValueError, "空槽"):
            require_editable(bytes(58), PROFILE, "box")


if __name__ == "__main__":
    unittest.main()
